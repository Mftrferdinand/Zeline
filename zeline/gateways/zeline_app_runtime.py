"""Zeline App runtime bridge — menjalankan agent ASLI dan menerjemahkan
callback-nya menjadi event SSE sesuai ``docs/SSE_EVENT_SCHEMA.md``.

Prinsip: tidak menduplikasi runtime. Semua eksekusi tetap milik
``zeline.agent.Zeline`` + ``ToolExecutor``; modul ini hanya adapter transport
(callback agent → event SSE) plus persistensi session/message untuk client.

Tidak ada jawaban terprogram/scripted di sini. Kalau provider mati, yang
dikirim ke client adalah ``stream.error`` — bukan teks palsu.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from zeline.agent import CANCELLED_REPLY, Zeline, ZelineError
from zeline import config

# Direktori data mengikuti app_data (menghormati ZELINE_APP_DATA_DIR) supaya
# dev/test bisa terisolasi dari data milik user.
from zeline import app_data


def _app_dir() -> Path:
    return app_data._app_dir()


def _history_dir() -> Path:
    return _app_dir() / "history"


def _messages_dir() -> Path:
    return _app_dir() / "messages"


# Kompatibilitas nama lama.
APP_DIR = _app_dir()
HISTORY_DIR = APP_DIR / "history"
MESSAGES_DIR = APP_DIR / "messages"

# Batas payload tool yang dikirim ke client mobile (hindari flooding UI).
TOOL_OUTPUT_LIMIT = 8_000
TOOL_CHUNK = 1_500

# ZELINE_APP_DEBUG=1 → cetak sebab berhentinya stream (cancel vs client putus).
DEBUG = os.environ.get("ZELINE_APP_DEBUG", "").strip() not in ("", "0", "false")

_LOCK = threading.RLock()
_AGENTS: dict[str, Zeline] = {}          # session_id → instance agent hidup
_CANCEL: dict[str, bool] = {}            # session_id → permintaan cancel
_ACTIVE: dict[str, str] = {}             # session_id → stream_id aktif
_STEER: dict[str, list[str]] = {}         # session_id → arahan user saat stream


# --------------------------------------------------------------- utilitas
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


#: Nama file per-session hanya boleh berisi karakter ini.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def safe_session_id(session_id: str) -> str:
    """Kembalikan ``session_id`` bila aman dipakai sebagai nama file.

    Session id datang dari path URL, jadi ia data yang dikendalikan pemanggil.
    Router memang sudah mencocokkan ``[\\w-]+``, tapi menaruh satu-satunya
    penjagaan di layer HTTP berarti setiap pemanggil baru (CLI, migrasi, test,
    gateway lain) mewarisi lubang yang sama. Penjagaan diletakkan di layer
    penyimpanan supaya ``..``/``/``/absolute path tidak pernah bisa menjadi
    nama file, siapa pun pemanggilnya.
    """
    value = str(session_id or "")
    if not _SAFE_ID.match(value):
        raise ValueError("invalid session id")
    return value


def _session_file(directory: Path, session_id: str) -> Path:
    """Path file JSON untuk satu session, dijamin berada di dalam ``directory``.

    Dua lapis, bukan satu: allowlist karakter di ``safe_session_id`` menolak id
    yang jelas hostile, lalu path hasilnya dinormalisasi dan diperiksa masih
    berada di bawah root. Lapis kedua tidak redundan — ia yang membuktikan
    containment tanpa bergantung pada kelengkapan regex, dan itu properti yang
    sebenarnya kita mau (tidak ada file di luar direktori data), bukan "id-nya
    kelihatan wajar".
    """
    name = safe_session_id(session_id) + ".json"
    root = os.path.normpath(str(directory))
    candidate = os.path.normpath(os.path.join(root, name))
    if not candidate.startswith(root + os.sep):
        raise ValueError("invalid session id")
    return Path(candidate)


def _ensure_dirs() -> None:
    for directory in (_app_dir(), _history_dir(), _messages_dir()):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, data: Any) -> None:
    _ensure_dirs()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)


# --------------------------------------------------- pemetaan tool → UI card
_TOOL_TITLES = {
    "run_shell": ("Running terminal command", "command"),
    "execute_code": ("Executing code", "code"),
    "read_file": ("Reading file", "path"),
    "write_file": ("Writing file", "path"),
    "edit_file": ("Editing file", "path"),
    "patch_file": ("Patching file", "path"),
    "search_files": ("Searching files", "pattern"),
    "web_search": ("Searching the web", "query"),
    "web_fetch": ("Fetching page", "url"),
    "deep_research": ("Deep research", "query"),
    "load_skill": ("Loading skill", "name"),
    "manage_skill": ("Maintaining skill", "name"),
    "add_memory": ("Saving memory", "text"),
    "list_memory": ("Reading memory", "query"),
    "delegate_task": ("Delegating subtask", "goal"),
    "generate_image": ("Generating image", "prompt"),
    "http_request": ("HTTP request", "url"),
    "runtime_info": ("Runtime info", ""),
    "recall_history": ("Recalling history", "query"),
}


def tool_presentation(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Judul + ringkasan input untuk kartu tool di client (tanpa secret)."""
    title, arg_key = _TOOL_TITLES.get(name, (name.replace("_", " ").title(), ""))
    summary = ""
    if name == "read_file" and isinstance(args, dict):
        path = str(args.get("path", "")).strip()
        try:
            start = max(1, int(args.get("offset", 1) or 1))
            limit = max(1, int(args.get("limit", 500) or 500))
            summary = f"{path} L{start}–{start + limit - 1}" if path else f"L{start}–{start + limit - 1}"
        except (TypeError, ValueError):
            summary = path
    if arg_key and isinstance(args, dict):
        summary = summary or str(args.get(arg_key, ""))
    if not summary and isinstance(args, dict) and args:
        first = next(iter(args.values()))
        summary = str(first)
    summary = " ".join(summary.split())
    return title, summary[:160]


def _clip_tool_output(text: str) -> str:
    body = str(text or "")
    if len(body) <= TOOL_OUTPUT_LIMIT:
        return body
    omitted = len(body) - TOOL_OUTPUT_LIMIT
    return body[:TOOL_OUTPUT_LIMIT] + f"\n… [{omitted} karakter dipotong untuk client]"


# ------------------------------------------------------- registry cancel/aktif
def stream_start(session_id: str, stream_id: str) -> None:
    with _LOCK:
        _CANCEL[session_id] = False
        _ACTIVE[session_id] = stream_id
        _STEER[session_id] = []


def stream_finish(session_id: str) -> None:
    with _LOCK:
        _CANCEL.pop(session_id, None)
        _ACTIVE.pop(session_id, None)
        _STEER.pop(session_id, None)


def stream_steer(session_id: str, text: str) -> bool:
    guidance = str(text or "").strip()
    with _LOCK:
        if session_id not in _ACTIVE or not guidance:
            return False
        _STEER.setdefault(session_id, []).append(guidance)
        return True


def take_stream_steer(session_id: str) -> str | None:
    with _LOCK:
        queue = _STEER.get(session_id) or []
        return queue.pop(0) if queue else None


def stream_cancel(session_id: str) -> tuple[bool, str | None]:
    """Idempoten: True hanya bila memang ada stream aktif yang dibatalkan."""
    with _LOCK:
        stream_id = _ACTIVE.get(session_id)
        if stream_id is None:
            return False, None
        _CANCEL[session_id] = True
        return True, stream_id


def is_cancelled(session_id: str) -> bool:
    with _LOCK:
        return bool(_CANCEL.get(session_id))


def active_streams() -> int:
    with _LOCK:
        return len(_ACTIVE)


# --------------------------------------------------------- provider resolution
def provider_refs() -> list[dict[str, Any]]:
    """Provider nyata dari config Zeline, key selalu ter-mask."""
    cfg = config.load_config()
    providers = cfg.get("providers", {}) or {}
    active = cfg.get("provider", {}) or {}
    refs: list[dict[str, Any]] = []
    for key, provider in providers.items():
        raw_key = str(provider.get("api_key", ""))
        refs.append({
            "id": key,
            "type": key,
            "name": str(provider.get("name", key)),
            "base_url": str(provider.get("base_url", "")),
            "protocol": str(provider.get("protocol", "openai")),
            "credential_status": "configured" if raw_key else "missing",
            "api_key_hint": ("••••" + raw_key[-4:].upper()) if len(raw_key) >= 4 else None,
            "models": [m for m in [str(provider.get("model", ""))] if m],
            "is_active": str(provider.get("base_url", "")) == str(active.get("base_url", "")),
        })
    return refs


def resolve_provider(provider_id: str) -> dict[str, Any]:
    """Ambil kredensial provider untuk dipakai runtime (JANGAN kirim ke client)."""
    cfg = config.load_config()
    providers = cfg.get("providers", {}) or {}
    if provider_id and provider_id in providers:
        return dict(providers[provider_id])
    return dict(cfg.get("provider", {}) or {})


def _slugify_provider_id(name: str, existing: dict[str, Any]) -> str:
    """ID provider stabil dari nama; unik terhadap yang sudah ada."""
    base = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-") or "provider"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def detect_models(base_url: str, api_key: str) -> list[str]:
    """Panggil GET {base_url}/models untuk mendeteksi model yang tersedia.

    Dipakai saat menambah provider: user cukup memberi base_url + API key,
    daftar model diambil otomatis (bukan diketik manual). Return list kosong
    bila endpoint tidak menjawab atau tidak berbentuk daftar model OpenAI.
    """
    base = str(base_url or "").rstrip("/")
    if not base:
        return []
    try:
        import requests
        response = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=8,
        )
        payload = response.json()
    except Exception:
        return []
    items = payload.get("data") if isinstance(payload, dict) else None
    models = [str(m.get("id")) for m in items
              if isinstance(m, dict) and m.get("id")] if isinstance(items, list) else []
    return sorted(dict.fromkeys(models))


def save_provider(name: str, base_url: str, api_key: str, *,
                  protocol: str = "openai", provider_id: str = "",
                  models: list[str] | None = None) -> dict[str, Any]:
    """Persist provider baru/diperbarui ke config, lalu kembalikan ref ter-mask.

    API key disimpan di config (0600) dan TIDAK pernah dikirim balik ke client.
    Model dideteksi otomatis lewat ``detect_models`` bila tidak diberikan.
    """
    cfg = config.stored_config_copy()
    providers = dict(cfg.get("providers", {}) or {})
    pid = provider_id or _slugify_provider_id(name, providers)
    detected = models if models else detect_models(base_url, api_key)
    entry = dict(providers.get(pid, {}))
    entry.update({
        "name": str(name).strip() or pid,
        "base_url": str(base_url).strip().rstrip("/"),
        "protocol": str(protocol or "openai"),
    })
    if api_key:
        entry["api_key"] = str(api_key)
    if detected:
        entry["model"] = detected[0]
        entry["models"] = detected
    providers[pid] = entry
    cfg["providers"] = providers
    config.save_config(cfg)
    ref = next((p for p in provider_refs() if p.get("id") == pid), None)
    if ref is not None:
        ref["models"] = detected or ref.get("models", [])
    return ref or {"id": pid, "name": entry["name"], "models": detected}


def delete_provider(provider_id: str) -> bool:
    """Hapus provider dari config. Return False bila tidak ada."""
    cfg = config.stored_config_copy()
    providers = dict(cfg.get("providers", {}) or {})
    if provider_id not in providers:
        return False
    providers.pop(provider_id, None)
    cfg["providers"] = providers
    config.save_config(cfg)
    return True


# ------------------------------------------------------------ agent instances
def _history_path(session_id: str) -> Path:
    return _session_file(_history_dir(), session_id)


def _messages_path(session_id: str) -> Path:
    return _session_file(_messages_dir(), session_id)


def get_agent_runtime(session_id: str, agent: dict[str, Any], tool_profile: str) -> Zeline:
    """Instance agent per session (history terjaga antar pesan)."""
    session_id = safe_session_id(session_id)
    with _LOCK:
        instance = _AGENTS.get(session_id)
    if instance is None:
        instance = Zeline(
            identity=f"zeline_app:{session_id}",
            tool_profile=tool_profile,
            system_extra=str(agent.get("system_instructions", "") or ""),
        )
        stored = _read_json(_history_path(session_id), [])
        if isinstance(stored, list) and stored:
            instance.load_history(stored)
        with _LOCK:
            _AGENTS[session_id] = instance

    provider = resolve_provider(str(agent.get("provider_id", "")))
    if provider.get("base_url"):
        instance.base_url = str(provider["base_url"]).rstrip("/")
    if provider.get("api_key"):
        instance.api_key = str(provider["api_key"])
    if provider.get("protocol"):
        instance.protocol = str(provider["protocol"])
    # Model per-agent menang atas default config.
    model = str(agent.get("model") or provider.get("model") or config.MODEL)
    instance.model = model
    # SSE menjanjikan assistant.delta ke client, dan cancel hanya bisa memutus
    # di tengah kalau ada loop baca yang bisa diperiksa. Keduanya butuh
    # streaming, jadi gateway memaksanya di instance-nya sendiri, terlepas dari
    # preferensi `agent.stream` yang berlaku untuk CLI.
    instance.stream_responses = True
    return instance


def persist_history(session_id: str, instance: Zeline) -> None:
    _write_json(_history_path(session_id), instance.export_history())


def drop_session_runtime(session_id: str) -> None:
    with _LOCK:
        _AGENTS.pop(session_id, None)
        _CANCEL.pop(session_id, None)
        _ACTIVE.pop(session_id, None)
        _STEER.pop(session_id, None)
    try:
        paths = (_history_path(session_id), _messages_path(session_id))
    except ValueError:
        # Id yang tak pernah bisa jadi nama file juga tak punya file untuk
        # dihapus. Registry di atas sudah dibersihkan, jadi delete tetap sukses.
        return
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ messages
def load_messages(session_id: str) -> list[dict[str, Any]]:
    # Reads tolerate a rejected id: "no such session" and "id that can never
    # name a file" look the same to a client, and a lookup should answer 404,
    # not 500. Writes still raise — storing under an unsafe name is a bug.
    try:
        path = _messages_path(session_id)
    except ValueError:
        return []
    data = _read_json(path, [])
    return data if isinstance(data, list) else []


def append_message(session_id: str, message: dict[str, Any]) -> dict[str, Any]:
    messages = load_messages(session_id)
    messages.append(message)
    _write_json(_messages_path(session_id), messages)
    return message


def update_last_message(session_id: str, patch: dict[str, Any]) -> None:
    messages = load_messages(session_id)
    if not messages:
        return
    messages[-1].update(patch)
    _write_json(_messages_path(session_id), messages)


def new_message(session_id: str, agent_id: str, role: str, content: str,
                status: str = "complete", **extra: Any) -> dict[str, Any]:
    message = {
        "id": "msg_" + secrets.token_hex(4),
        "session_id": session_id,
        "agent_id": agent_id,
        "role": role,
        "content": content,
        "created_at": now_iso(),
        "status": status,
        "attachments": extra.pop("attachments", []),
        "metadata": extra.pop("metadata", {}),
    }
    message.update(extra)
    return message


# ------------------------------------------------------------- generation run
def run_generation(
    session_id: str,
    agent: dict[str, Any],
    user_text: str,
    emit: Callable[[dict[str, Any]], bool],
    tool_profile: str = "safe",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Jalankan satu turn agent ASLI, streaming event ke ``emit``.

    ``emit(event)`` harus mengembalikan False bila client sudah putus; saat itu
    generation dihentikan lewat ``should_stop`` milik agent.
    """
    msg_id = message_id or ("msg_" + secrets.token_hex(4))
    instance = get_agent_runtime(session_id, agent, tool_profile)

    alive = {"ok": True}
    segments: list[str] = [""]     # teks per-bubble; bubble baru setelah tool
    full: list[str] = []
    tool_log: list[dict[str, Any]] = []
    open_tools: dict[str, dict[str, Any]] = {}

    def push(event: dict[str, Any]) -> None:
        if not alive["ok"]:
            return
        if not emit(event):
            alive["ok"] = False
            if DEBUG:
                print(f"  [zeline-app] client putus saat emit {event.get('type')} "
                      f"session={session_id}", flush=True)

    def should_stop() -> bool:
        if not alive["ok"]:
            return True
        if is_cancelled(session_id):
            if DEBUG:
                print(f"  [zeline-app] stop karena flag cancel session={session_id}",
                      flush=True)
            return True
        return False

    def take_steer() -> str | None:
        return take_stream_steer(session_id)

    def on_stream_delta(piece: str) -> None:
        if should_stop():
            return
        segments[-1] += piece
        full.append(piece)
        push({"type": "assistant.delta", "message_id": msg_id, "content": piece})

    def on_iteration(index: int, total: int) -> None:
        if index == 1:
            push({"type": "assistant.thinking", "message_id": msg_id,
                  "state": "planning", "round": index, "max_rounds": total})

    def on_tool(name: str, args: dict[str, Any]) -> None:
        title, summary = tool_presentation(name, args if isinstance(args, dict) else {})
        call_id = "tool_" + secrets.token_hex(4)
        open_tools.setdefault(name, {})
        open_tools[name] = {"id": call_id, "t0": time.time()}
        tool_log.append({"tool_call_id": call_id, "tool": name, "title": title})
        segments.append("")   # teks setelah tool masuk bubble baru
        push({"type": "tool.started", "tool_call_id": call_id, "tool": name,
              "title": title, "input_summary": summary})

    def on_tool_result(name: str, args: dict[str, Any], result: str) -> None:
        entry = open_tools.get(name) or {"id": "tool_" + secrets.token_hex(4), "t0": time.time()}
        call_id = entry["id"]
        duration = int((time.time() - entry["t0"]) * 1000)
        body = _clip_tool_output(result)
        for start in range(0, len(body), TOOL_CHUNK):
            push({"type": "tool.output", "tool_call_id": call_id,
                  "content": body[start:start + TOOL_CHUNK]})
        failed = str(result or "").startswith("ERROR")
        if failed:
            push({"type": "tool.failed", "tool_call_id": call_id, "status": "failed",
                  "error_code": "TOOL_FAILURE", "message": body[:400],
                  "duration_ms": duration})
        else:
            push({"type": "tool.completed", "tool_call_id": call_id,
                  "status": "success", "duration_ms": duration})
        for item in tool_log:
            if item["tool_call_id"] == call_id:
                item["status"] = "failed" if failed else "success"
                item["duration_ms"] = duration

    push({"type": "stream.started", "stream_id": _ACTIVE.get(session_id, ""),
          "message_id": msg_id, "agent_id": str(agent.get("id", "")),
          "model": instance.model})

    error: str | None = None
    try:
        final = instance.send(
            user_text,
            on_tool=on_tool,
            on_tool_result=on_tool_result,
            on_iteration=on_iteration,
            should_stop=should_stop,
            take_steer=take_steer,
            on_stream_delta=on_stream_delta,
        )
    except ZelineError as exc:
        final = ""
        error = str(exc)[:400]
    except Exception as exc:  # jangan bocorkan trace ke client
        final = ""
        error = f"{exc.__class__.__name__}: {str(exc)[:200]}"

    streamed = "".join(full)
    cancelled = is_cancelled(session_id) or (isinstance(final, str) and final == CANCELLED_REPLY)

    # Provider non-streaming: tidak ada delta sama sekali → kirim final sebagai teks.
    if not error and not cancelled and final and not streamed:
        segments[-1] = final
        push({"type": "assistant.delta", "message_id": msg_id, "content": final})
        streamed = final

    tail = segments[-1] if segments else ""
    result: dict[str, Any] = {
        "message_id": msg_id,
        "content": streamed,
        "tail": tail,
        "final": final if isinstance(final, str) else "",
        "tool_events": tool_log,
        "model": instance.model,
        "status": "complete",
        "error": error,
    }

    if error:
        result["status"] = "failed"
        error_code = "RATE_LIMITED" if ("429" in error or "rate limit" in error.lower()) else "PROVIDER_ERROR"
        push({"type": "stream.error", "message_id": msg_id,
              "error_code": error_code, "message": error,
              "partial_content": streamed})
    elif cancelled:
        result["status"] = "cancelled"
        push({"type": "stream.cancelled", "stream_id": _ACTIVE.get(session_id, ""),
              "message_id": msg_id, "reason": "client_requested",
              "partial_content": streamed, "tail_content": tail})
    else:
        push({"type": "assistant.completed", "message_id": msg_id,
              "content": tail, "full_content": streamed, "status": "complete",
              "metadata": {"model": instance.model, "chars": len(streamed),
                           "tool_calls": len(tool_log)}})
        # Refleksi nyata, best-effort, hanya untuk task berbobot pada profile full.
        # Jangan kirim status palsu bila tidak ada skill yang benar-benar dibuat/
        # diperbaiki. Jawaban final sudah terkirim sehingga UI tidak tertahan.
        try:
            improvement = instance.reflect(min_tool_calls=5)
        except Exception:
            improvement = None
        if improvement:
            push({"type": "self.improvement", "message_id": msg_id,
                  "detail": str(improvement)[:1000]})

    try:
        persist_history(session_id, instance)
    except OSError:
        pass
    return result
