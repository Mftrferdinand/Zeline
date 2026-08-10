"""Inti Zeline: LLM di dalam loop yang boleh memakai tool.

Setiap object ``Zeline`` milik satu identity percakapan dan satu tool profile.
Jadi Telegram user A tidak pernah berbagi history atau memory dengan user B.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable

import requests

from zeline import config
from zeline import skills
from zeline.tools import ToolExecutor


class ZelineError(RuntimeError):
    """Error yang aman ditampilkan gateway sebagai gangguan internal."""


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
            raise ZelineError("Provider memberi respons yang bukan JSON valid.") from exc
    if not isinstance(value, dict):
        raise ZelineError("Provider memberi bentuk respons yang tidak dikenal.")
    if value.get("error"):
        error = value["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise ZelineError(f"Provider menolak request: {str(message)[:300]}")
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
    ):
        self.identity = identity or "cli:local"
        self.base_url = config.BASE_URL
        self.api_key = config.API_KEY
        self.model = config.MODEL
        self.protocol = config.PROTOCOL
        self.executor = ToolExecutor(
            identity=self.identity,
            profile=tool_profile or config.CLI_TOOL_PROFILE,
            workspace=workspace or config.WORKSPACE,
        )
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    config.SYSTEM_PROMPT
                    + self.executor.memory.prompt_block()
                    + skills.skills_block(include_private=self.executor.profile == "full")
                    + system_extra
                    + f"\n\nRuntime aktif (non-secret): model={self.model}; provider={self.base_url}; protocol={self.protocol}; profile={self.executor.profile}. "
                    + "\n\nSimpan fakta jangka panjang yang benar-benar berguna memakai add_memory. "
                    "Jika tugas sesuai skill yang tersedia, panggil load_skill terlebih dahulu. "
                    "Model ID, provider base URL, protokol, identitas runtime, dan daftar tool bukan rahasia; "
                    "jawab pertanyaan tentang itu memakai runtime_info. API key, token, dan secret tetap dilarang diungkap."
                ),
            }
        ]

    def _call_llm(self) -> dict[str, Any]:
        if not self.api_key:
            raise ZelineError("API key belum dikonfigurasi. Jalankan `zeline setup`.")
        if not self.base_url or not self.model:
            raise ZelineError("Provider belum lengkap. Jalankan `zeline setup`.")

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "zeline/0.1.0",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": copy.deepcopy(self.messages),
            "tools": self.executor.schemas,
            "tool_choice": "auto",
            "temperature": 0.7,
            "stream": False,
        }

        if self.protocol == "anthropic":
            endpoint = f"{self.base_url}/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "User-Agent": "zeline/0.1.0",
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
                "tools": [{"name": tool["function"]["name"], "description": tool["function"]["description"], "input_schema": tool["function"]["parameters"]} for tool in self.executor.schemas],
                "max_tokens": 4096,
            }

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
        except requests.RequestException as exc:
            raise ZelineError(f"Gagal menghubungi provider: {exc.__class__.__name__}.") from exc
        if not response.ok:
            raise ZelineError(f"Provider HTTP {response.status_code}.")
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
            raise ZelineError("Provider tidak mengembalikan pilihan respons.") from exc
        if not isinstance(message, dict):
            raise ZelineError("Provider mengembalikan message yang tidak valid.")
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
        should_stop: Callable[[], bool] | None = None,
        take_steer: Callable[[], str | None] | None = None,
    ) -> str:
        text = user_input.strip()
        if not text:
            return "Tulis pesan dulu ya."
        if len(text) > 16_000:
            return "Pesan terlalu panjang (maksimum 16.000 karakter)."
        self.messages.append({"role": "user", "content": text})

        for _ in range(config.MAX_TOOL_ROUNDS):
            if should_stop and should_stop():
                return "Stopped."
            message = self._call_llm()
            if should_stop and should_stop():
                return "Stopped."
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                content = str(message.get("content") or "").strip()
                self.messages.append({"role": "assistant", "content": content})
                self._trim_history()
                return content or "(provider tidak mengirim jawaban teks)"

            if not isinstance(tool_calls, list):
                raise ZelineError("Format tool call dari provider tidak valid.")

            # Urutan ini wajib untuk OpenAI-compatible tool calling.
            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
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
                if on_tool:
                    on_tool(name, args)
                result = self.executor.run(name, args)
                steer_text = take_steer() if take_steer else None
                if steer_text:
                    result += f"\n\n[User steering — follow this guidance now: {steer_text}]"
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tool_call.get("id", "")),
                        "content": result,
                    }
                )

        self._trim_history()
        return "Aku berhenti karena terlalu banyak putaran tool. Coba tugas yang lebih spesifik."

