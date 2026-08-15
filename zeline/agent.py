"""Inti Zeline: LLM di dalam loop yang boleh memakai tool.

Setiap object ``Zeline`` milik satu identity percakapan dan satu tool profile.
Jadi Telegram user A tidak pernah berbagi history atau memory dengan user B.
"""
from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import requests

from zeline import __version__, config
from zeline import skills
from zeline.tools import ToolExecutor


class ZelineError(RuntimeError):
    """Error yang aman ditampilkan gateway sebagai gangguan internal."""


# Tool read-only (tanpa efek samping) yang aman dijalankan paralel dalam satu
# giliran. Tool yang menulis (file/memory/skill/shell) tetap serial demi urutan
# dan keamanan thread. MCP tool tidak diketahui sifatnya → diperlakukan serial.
_PARALLEL_SAFE_TOOLS = frozenset({
    "runtime_info",
    "list_memory",
    "load_skill",
    "read_file",
    "search_files",
    "web_search",
    "web_fetch",
    "deep_research",
})


def _parse_response(text: str) -> dict[str, Any]:
    """Parse normal JSON dan quirk router yang mengirim JSON+trailing SSE."""
    cleaned = text.strip()
    if cleaned.startswith("data:"):
        cleaned = cleaned[5:].strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned)
        except json.JSONDecodeError as exc:
            raise ZelineError("Provider returned a non-JSON response.") from exc
    if not isinstance(value, dict):
        raise ZelineError("Provider returned an unrecognized response shape.")
    if value.get("error"):
        error = value["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise ZelineError(f"Provider rejected the request: {str(message)[:300]}")
    return value


class Zeline:
    """Satu sesi agent untuk satu user/chat.

    ``tool_profile``:
      - safe: memory + load skill (default gateway publik)
      - workspace: safe + file di workspace
      - full: workspace + shell, default CLI owner
    """

    def __init__(
        self,
        identity: str = "cli:local",
        tool_profile: str | None = None,
        workspace: str | None = None,
        system_extra: str = "",
        depth: int = 0,
    ):
        self.identity = identity or "cli:local"
        self.base_url = config.BASE_URL
        self.api_key = config.API_KEY
        self.model = config.MODEL
        self.protocol = config.PROTOCOL
        self.depth = int(depth)
        self.executor = ToolExecutor(
            identity=self.identity,
            profile=tool_profile or config.CLI_TOOL_PROFILE,
            workspace=workspace or config.WORKSPACE,
            depth=self.depth,
        )
        self._system_extra = system_extra
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()}
        ]
        # Jejak aktivitas turn terakhir → dipakai untuk memutuskan apakah sesi
        # cukup "berbobot" untuk dijalankan refleksi self-improvement.
        self.last_turn_tool_calls: int = 0

    def _build_system_prompt(self) -> str:
        return (
            config.SYSTEM_PROMPT
            + self.executor.memory.prompt_block()
            + skills.skills_block(include_private=self.executor.profile == "full")
            + self._system_extra
            + f"\n\nRuntime aktif (non-secret): model={self.model}; provider={self.base_url}; protocol={self.protocol}; profile={self.executor.profile}. "
            + "\n\nSimpan fakta jangka panjang yang benar-benar berguna memakai add_memory. "
            "Jika tugas sesuai skill yang tersedia, panggil load_skill terlebih dahulu. "
            "Model ID, provider base URL, protokol, identitas runtime, dan daftar tool bukan rahasia; "
            "jawab pertanyaan tentang itu memakai runtime_info. API key, token, dan secret tetap dilarang diungkap."
        )

    def reload_provider(self) -> None:
        """Adopsi provider aktif (model/base_url/key/protocol) TANPA menghapus

        history percakapan. Dipakai saat user /model switch: ganti otak, ingatan
        tetap. Baris runtime non-secret di system prompt ikut disegarkan supaya
        info model akurat, tapi seluruh pesan user/assistant sebelumnya dijaga.
        """
        self.base_url = config.BASE_URL
        self.api_key = config.API_KEY
        self.model = config.MODEL
        self.protocol = config.PROTOCOL
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._build_system_prompt()

    def export_history(self) -> list[dict[str, Any]]:
        """Salinan message history penuh (termasuk system) untuk dipersist."""
        return copy.deepcopy(self.messages)

    def load_history(self, messages: list[dict[str, Any]]) -> None:
        """Pulihkan history dari penyimpanan, mempertahankan system prompt aktif.

        System prompt selalu diambil dari instance saat ini (bisa berubah kalau
        model/nama/skill berubah), jadi kita buang system lama dari data tersimpan
        dan sambung sisanya di belakang system yang fresh.
        """
        if not messages:
            return
        system = self.messages[0]
        restored = [m for m in messages if m.get("role") != "system"]
        # Jangan mulai dari assistant/tool orphan (protocol tool-call).
        start = next((i for i, m in enumerate(restored) if m.get("role") == "user"), 0)
        self.messages = [system, *restored[start:]]

    def _call_llm(
        self,
        use_tools: bool = True,
        on_stream_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ZelineError("API key not configured. Run `zeline setup`.")
        if not self.base_url or not self.model:
            raise ZelineError("Provider not fully configured. Run `zeline setup`.")

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"zeline/{__version__}",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": copy.deepcopy(self.messages),
            "temperature": 0.7,
            "stream": bool(getattr(config, "STREAM_RESPONSES", True)),
        }
        if use_tools:
            payload["tools"] = self.executor.schemas
            payload["tool_choice"] = "auto"

        if self.protocol == "anthropic":
            endpoint = f"{self.base_url}/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "User-Agent": f"zeline/{__version__}",
            }
            messages: list[dict[str, Any]] = []
            for item in self.messages[1:]:
                role = str(item.get("role", "user"))
                if role == "tool":
                    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(item.get("tool_call_id", "")), "content": str(item.get("content", ""))}]})
                elif role == "assistant" and item.get("tool_calls"):
                    blocks: list[dict[str, Any]] = []
                    if item.get("content"):
                        blocks.append({"type": "text", "text": str(item["content"])})
                    for call in item["tool_calls"]:
                        function = call.get("function", {})
                        try:
                            arguments = json.loads(function.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            arguments = {}
                        blocks.append({"type": "tool_use", "id": str(call.get("id", "")), "name": str(function.get("name", "")), "input": arguments})
                    messages.append({"role": "assistant", "content": blocks})
                elif role in {"user", "assistant"}:
                    messages.append({"role": role, "content": str(item.get("content", ""))})
            payload = {
                "model": self.model,
                "system": str(self.messages[0].get("content", "")),
                "messages": messages,
                "max_tokens": 4096,
                "stream": bool(getattr(config, "STREAM_RESPONSES", True)),
            }
            if use_tools:
                payload["tools"] = [{"name": tool["function"]["name"], "description": tool["function"]["description"], "input_schema": tool["function"]["parameters"]} for tool in self.executor.schemas]

        stream = bool(payload.get("stream"))
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=180, stream=stream)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.Timeout) as exc:
            raise ZelineError(
                f"The model '{self.model}' did not respond within 180s (request timed out). "
                "The provider or route is likely overloaded or stalled — try again, or switch to a faster model with /model."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise ZelineError(
                f"Could not connect to the provider at {self.base_url}. "
                "Check that the router/proxy is running and the base URL is correct."
            ) from exc
        except requests.RequestException as exc:
            raise ZelineError(
                f"Network error while contacting the provider ({exc.__class__.__name__}). Please try again."
            ) from exc
        # requests memaksa ISO-8859-1 untuk Content-Type text/* tanpa charset
        # (termasuk text/event-stream saat streaming). Itu bikin karakter non-ASCII
        # seperti panah → dan emoji rusak jadi mojibake (â…). Provider LLM selalu
        # kirim UTF-8, jadi paksa UTF-8 sebelum decode teks/stream apa pun.
        response.encoding = "utf-8"
        if not response.ok:
            hint = ""
            if response.status_code in (401, 403):
                hint = " — the API key is invalid or unauthorized. Update it with `zeline setup`."
            elif response.status_code == 404:
                hint = f" — the model '{self.model}' was not found on this provider. Pick another with /model."
            elif response.status_code == 429:
                hint = " — rate limited or out of credits on the provider."
            elif response.status_code >= 500:
                hint = " — the provider is having a server-side problem. Try again shortly."
            raise ZelineError(f"The provider returned HTTP {response.status_code}{hint}")

        if stream:
            if self.protocol == "anthropic":
                return self._consume_anthropic_stream(response, on_stream_delta)
            return self._consume_openai_stream(response, on_stream_delta)

        parsed = _parse_response(response.text)

        if self.protocol == "anthropic":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in parsed.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_use":
                    tool_calls.append({"id": str(block.get("id", "")), "type": "function", "function": {"name": str(block.get("name", "")), "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)}})
            message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            return message

        try:
            message = parsed["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ZelineError("Provider returned no response choices.") from exc
        if not isinstance(message, dict):
            raise ZelineError("Provider returned an invalid message.")
        return message

    def _consume_openai_stream(
        self,
        response: "requests.Response",
        on_stream_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Rakit satu message dari SSE OpenAI-compatible (choices[].delta).

        Streaming = token mengalir seketika, jadi tidak ada jeda diam panjang
        yang memicu read-timeout pada model 'thinking'. Kita rakit ulang konten
        teks + tool_calls (yang datang terpotong per-delta) menjadi bentuk
        message non-stream yang sama, supaya sisa loop agent tidak berubah.
        """
        content_parts: list[str] = []
        # tool_calls dirakit per index; argumen string di-append bertahap.
        tool_map: dict[int, dict[str, Any]] = {}
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = str(raw_line).strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    if line == "[DONE]":
                        break
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    error = chunk["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise ZelineError(f"Provider rejected the request: {str(detail)[:300]}")
                choices = chunk.get("choices") if isinstance(chunk, dict) else None
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content_parts.append(str(piece))
                    if on_stream_delta:
                        try:
                            on_stream_delta(str(piece))
                        except Exception:
                            pass
                for call in delta.get("tool_calls") or []:
                    index = int(call.get("index", 0))
                    slot = tool_map.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if call.get("id"):
                        slot["id"] = str(call["id"])
                    function = call.get("function") or {}
                    if function.get("name"):
                        slot["function"]["name"] = str(function["name"])
                    if function.get("arguments"):
                        slot["function"]["arguments"] += str(function["arguments"])
        except (requests.exceptions.RequestException,) as exc:
            raise ZelineError(
                f"The stream from '{self.model}' was interrupted ({exc.__class__.__name__}). Please try again."
            ) from exc
        finally:
            response.close()

        message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
        if tool_map:
            message["tool_calls"] = [tool_map[index] for index in sorted(tool_map)]
        return message

    def _consume_anthropic_stream(
        self,
        response: "requests.Response",
        on_stream_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Rakit satu message dari SSE Anthropic (content_block_delta events).

        Menangani text_delta (jawaban) dan input_json_delta (argumen tool_use).
        Dikembalikan dalam bentuk message OpenAI-compatible seperti jalur
        non-stream anthropic, supaya loop agent seragam.
        """
        text_parts: list[str] = []
        # index blok -> {id, name, json}
        blocks: dict[int, dict[str, Any]] = {}
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = str(raw_line).strip()
                if line.startswith("event:"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                etype = event.get("type", "")
                if etype == "error":
                    detail = event.get("error", {})
                    message_text = detail.get("message") if isinstance(detail, dict) else str(detail)
                    raise ZelineError(f"Provider rejected the request: {str(message_text)[:300]}")
                if etype == "content_block_start":
                    index = int(event.get("index", 0))
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        blocks[index] = {"id": str(block.get("id", "")), "name": str(block.get("name", "")), "json": ""}
                elif etype == "content_block_delta":
                    index = int(event.get("index", 0))
                    delta = event.get("delta") or {}
                    dtype = delta.get("type", "")
                    if dtype == "text_delta":
                        piece = str(delta.get("text", ""))
                        text_parts.append(piece)
                        if piece and on_stream_delta:
                            try:
                                on_stream_delta(piece)
                            except Exception:
                                pass
                    elif dtype == "input_json_delta" and index in blocks:
                        blocks[index]["json"] += str(delta.get("partial_json", ""))
                elif etype == "message_stop":
                    break
        except (requests.exceptions.RequestException,) as exc:
            raise ZelineError(
                f"The stream from '{self.model}' was interrupted ({exc.__class__.__name__}). Please try again."
            ) from exc
        finally:
            response.close()

        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
        tool_calls: list[dict[str, Any]] = []
        for index in sorted(blocks):
            block = blocks[index]
            arguments = block["json"] or "{}"
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {"name": block["name"], "arguments": arguments},
            })
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def _trim_history(self) -> None:
        """Batasi history tanpa memulai context dari assistant/tool orphan.

        Tool-call protocol mengharuskan ``assistant(tool_calls)`` diikuti semua
        ``tool`` result terkait. Maka kita hanya memotong pada awal user turn,
        bukan sekadar `messages[-N:]`.
        """
        maximum = 60
        if len(self.messages) <= maximum + 1:
            return
        system = self.messages[0]
        tail = self.messages[-maximum:]
        # Cari awal user turn pertama di window; buang sisa turn sebelumnya.
        start = next((index for index, message in enumerate(tail) if message.get("role") == "user"), len(tail))
        trimmed = tail[start:]
        # Fallback defensif bila tidak ada user message (seharusnya tidak terjadi
        # karena trim dipanggil setelah turn final selesai).
        if not trimmed:
            trimmed = []
        self.messages = [system, *trimmed]

    def send(
        self,
        user_input: str,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_result: Callable[[str, dict[str, Any], str], None] | None = None,
        on_iteration: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        take_steer: Callable[[], str | None] | None = None,
        on_narration: Callable[[str], None] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
    ) -> str:
        text = user_input.strip()
        if not text:
            return "Please write a message first."
        if len(text) > 16_000:
            return "Message too long (maximum 16,000 characters)."
        self.messages.append({"role": "user", "content": text})
        self.last_turn_tool_calls = 0
        turn_started = time.monotonic()
        repeated_failures = 0  # tool call berturut yang balik ERROR

        for iteration in range(1, config.MAX_TOOL_ROUNDS + 1):
            if should_stop and should_stop():
                return "Stopped."
            # Batas waktu wall-clock per turn: kalau sudah lewat, jangan lanjut
            # loop tool (mis. web_search yang gagal berulang) — paksa jawaban
            # final dari data yang ada. Ini mencegah "Processing" 10 menit.
            if time.monotonic() - turn_started > config.MAX_TURN_SECONDS:
                answer = self._force_final_answer(should_stop, on_stream_delta=on_stream_delta)
                self._trim_history()
                return answer
            if on_iteration:
                on_iteration(iteration, config.MAX_TOOL_ROUNDS)
            message = self._call_llm(on_stream_delta=on_stream_delta)
            if should_stop and should_stop():
                return "Stopped."
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                content = str(message.get("content") or "").strip()
                self.messages.append({"role": "assistant", "content": content})
                self._trim_history()
                return content or "(provider tidak mengirim jawaban teks)"

            if not isinstance(tool_calls, list):
                raise ZelineError("Invalid tool call format from the provider.")
            self.last_turn_tool_calls += len(tool_calls)

            # Narasi live: teks yang menyertai tool call (mis. "Gua cek dulu
            # konfignya lalu benerin") adalah kalimat rencana model. Kirim ke
            # user sebagai bubble tersendiri SEBELUM tool jalan — inilah yang
            # bikin alurnya kebaca seperti Selena/Hermes (bubble penjelasan →
            # terminal → temuan), bukan diam lalu tiba-tiba dump panjang.
            narration = str(message.get("content") or "").strip()
            if narration and on_narration:
                on_narration(narration)

            # Urutan ini wajib untuk OpenAI-compatible tool calling.
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            # Parse setiap tool call sekali (nama + argumen) dengan urutan dijaga.
            parsed_calls: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
            for tool_call in tool_calls:
                function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
                name = str(function.get("name", ""))
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                parsed_calls.append((tool_call, name, args))

            # Bila model meminta >1 tool dan SEMUANYA read-only aman-paralel,
            # jalankan bareng dalam thread pool (percepat riset/baca banyak file).
            # Selain itu, jalankan serial demi urutan & keamanan tool yang menulis.
            run_parallel = (
                len(parsed_calls) > 1
                and all(name in _PARALLEL_SAFE_TOOLS for _tc, name, _a in parsed_calls)
            )
            if on_tool:
                for _tc, name, args in parsed_calls:
                    on_tool(name, args)

            if run_parallel:
                with ThreadPoolExecutor(max_workers=min(len(parsed_calls), 5)) as pool:
                    results = list(pool.map(lambda ca: self.executor.run(ca[1], ca[2]), parsed_calls))
            else:
                results = [self.executor.run(name, args) for _tc, name, args in parsed_calls]

            steer_text = take_steer() if take_steer else None
            for (tool_call, name, args), result in zip(parsed_calls, results):
                if on_tool_result:
                    on_tool_result(name, args, result)
                if steer_text:
                    result += f"\n\n[User steering — follow this guidance now: {steer_text}]"
                    steer_text = None  # sertakan sekali saja, di tool result pertama
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tool_call.get("id", "")),
                        "content": result,
                    }
                )

            # Anti-loop: kalau SEMUA tool di ronde ini balik ERROR, hitung sebagai
            # kegagalan beruntun. Setelah beberapa ronde gagal berturut (mis.
            # web_search mati di jaringan ini), berhenti nge-hajar tool — paksa
            # jawaban final dari data yang ada, jangan sampai 20 ronde × detik.
            if results and all(str(r).startswith("ERROR") for r in results):
                repeated_failures += 1
                if repeated_failures >= config.MAX_REPEATED_TOOL_FAILURES:
                    answer = self._force_final_answer(should_stop, on_stream_delta=on_stream_delta)
                    self._trim_history()
                    return answer
            else:
                repeated_failures = 0

        # Putaran tool habis. Jangan menyerah tanpa jawaban: paksa satu panggilan
        # terakhir TANPA tool agar model menyintesis data yang sudah dikumpulkan.
        answer = self._force_final_answer(should_stop, on_stream_delta=on_stream_delta)
        self._trim_history()
        return answer

    def _force_final_answer(
        self,
        should_stop: Callable[[], bool] | None = None,
        on_stream_delta: Callable[[str], None] | None = None,
    ) -> str:
        """Minta jawaban final tanpa tool setelah batas putaran tercapai.

        Tanpa ini, tugas riset yang butuh banyak fetch akan berakhir dengan
        pesan 'terlalu banyak putaran' tanpa hasil. Di sini model dipaksa
        merangkum bukti yang sudah ada menjadi jawaban.
        """
        if should_stop and should_stop():
            return "Stopped."
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "Batas pemakaian tool tercapai. Berdasarkan semua informasi "
                    "yang sudah kamu kumpulkan sejauh ini, tulis jawaban final "
                    "terbaik sekarang untuk pertanyaan awalku. Jangan memanggil "
                    "tool lagi. Kalau ada bagian yang belum sempat diverifikasi, "
                    "sebutkan singkat, tapi tetap berikan jawaban yang berguna."
                ),
            }
        )
        try:
            message = self._call_llm(use_tools=False, on_stream_delta=on_stream_delta)
        except ZelineError:
            return (
                "Aku sudah mengumpulkan banyak data tapi butuh lebih banyak "
                "langkah untuk merampungkannya. Coba persempit pertanyaannya ya."
            )
        content = str(message.get("content") or "").strip()
        self.messages.append({"role": "assistant", "content": content})
        return content or (
            "Aku sudah mengumpulkan banyak data tapi belum bisa merangkumnya. "
            "Coba persempit pertanyaannya ya."
        )

    def reflect(self, min_tool_calls: int = 5) -> str | None:
        """Self-improvement review di akhir sesi penting (profile full saja).

        Menyuruh model meninjau percakapan yang baru saja terjadi lalu, bila ada
        prosedur reusable / pelajaran nyata, MENYIMPAN atau MEMPERBAIKI skill via
        tool save_skill/update_skill. Hanya dijalankan untuk sesi yang cukup
        berbobot (>= ``min_tool_calls`` tool call) supaya obrolan ringan tidak
        memicu skill sampah. Mengembalikan ringkasan tindakan, atau None bila
        tidak ada yang perlu disimpan / sesi terlalu ringan.
        """
        if self.executor.profile != "full":
            return None
        if self.last_turn_tool_calls < min_tool_calls:
            return None
        # Snapshot history saat ini; refleksi tidak boleh mencemari percakapan
        # utama, jadi kita kerjakan di salinan pesan yang dibuang setelah selesai.
        saved_messages = copy.deepcopy(self.messages)
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "REFLEKSI SELF-IMPROVEMENT (internal, jangan tampilkan ke user). "
                    "Tinjau singkat percakapan barusan. "
                    "(A) Apakah ada prosedur reusable, alur kerja non-trivial, atau "
                    "error tricky yang berhasil diatasi dan layak jadi skill? "
                    "- Kalau YA dan belum ada skill-nya: panggil save_skill (nama jelas, "
                    "isi: kapan dipakai, langkah bernomor + command persis, pitfalls). "
                    "- Kalau skill yang dipakai ternyata kurang/salah: panggil update_skill "
                    "untuk memperbaikinya. "
                    "(B) Apakah user MENGOREKSI kamu berulang tentang hal yang sama "
                    "(mis. edit/revisi yang katanya 'masih sama/nggak berubah', font/warna/"
                    "layout yang harus lebih presisi, atau kamu menimpa file dgn versi lama)? "
                    "Kalau YA: panggil add_memory berisi pelajaran ringkas & deklaratif biar "
                    "tidak terulang (mis. 'User sering koreksi UI kecil beruntun; wajib "
                    "read_file dulu lalu edit bagian spesifik, jangan regenerate dari nol'). "
                    "- Kalau TIDAK ada yang layak disimpan: jangan panggil tool apa pun dan "
                    "jawab persis 'NO_ACTION'. "
                    "Jangan menyimpan hal sepele/sekali-pakai atau rahasia."
                ),
            }
        )
        actions: list[str] = []
        try:
            for _ in range(3):  # maksimal beberapa langkah tool untuk refleksi
                message = self._call_llm()
                tool_calls = message.get("tool_calls")
                if not tool_calls or not isinstance(tool_calls, list):
                    break
                self.messages.append(
                    {"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls}
                )
                for tool_call in tool_calls:
                    function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
                    name = str(function.get("name", ""))
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                        if not isinstance(args, dict):
                            args = {}
                    except json.JSONDecodeError:
                        args = {}
                    result = self.executor.run(name, args)
                    if name in {"save_skill", "update_skill"} and not result.startswith("ERROR"):
                        actions.append(result)
                    self.messages.append(
                        {"role": "tool", "tool_call_id": str(tool_call.get("id", "")), "content": result}
                    )
        except ZelineError:
            actions = actions  # refleksi bersifat best-effort; error diabaikan
        finally:
            # Buang jejak refleksi dari history utama supaya tidak mengganggu
            # konteks percakapan berikutnya.
            self.messages = saved_messages
        return "\n".join(actions) if actions else None

