"""Gateway Telegram Aesora via raw Bot API long polling.

Tidak memakai SDK Telegram agar instalasi tetap kecil dan mudah di Termux.
Setiap chat mendapat identity ``telegram:<chat_id>`` sendiri sehingga history
+ memory tidak pernah tercampur dengan user lain.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import requests

from aesora import config
from aesora.agent import AesoraError

API_TEMPLATE = "https://api.telegram.org/bot{token}"
FILE_API_TEMPLATE = "https://api.telegram.org/file/bot{token}/{file_path}"
OFFSET_FILE = config.STATE_DIR / "telegram-offset.json"
TELEGRAM_MESSAGE_LIMIT = 4_000
TELEGRAM_TEXT_FILE_LIMIT = 256 * 1024
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".py", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".htm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_ARCHIVE_ENTRIES = 64
MAX_ARCHIVE_TEXT_BYTES = 256 * 1024


def info() -> dict[str, str]:
    return {
        "label": "Telegram",
        "hint": "Buat bot melalui @BotFather, lalu tempel token Bot API.",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    token = str(cfg.get("token", "")).strip()
    if not token:
        return ["token Telegram kosong"]
    if ":" not in token:
        return ["format token Telegram terlihat tidak valid"]
    profile = str(cfg.get("tool_profile", "safe"))
    if profile not in {"safe", "workspace", "full"}:
        return [f"tool_profile Telegram tidak valid: {profile}"]
    allowed = cfg.get("allowed", [])
    if not isinstance(allowed, list):
        return ["allowed Telegram harus berupa list chat ID"]
    return []


def _api_call(api: str, method: str, *, timeout: int = 65, **params: Any) -> dict[str, Any] | None:
    """Panggil Bot API; error network tidak boleh menghentikan gateway."""
    try:
        response = requests.post(f"{api}/{method}", json=params, timeout=timeout)
        payload = response.json()
        if response.ok and payload.get("ok"):
            return payload
        description = str(payload.get("description", "HTTP error"))[:160] if isinstance(payload, dict) else "HTTP error"
        print(f"  [telegram] {method} gagal: {description}", flush=True)
    except (requests.RequestException, ValueError) as exc:
        print(f"  [telegram] {method} gagal: {exc.__class__.__name__}", flush=True)
    return None


def _split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Pecah respons tanpa memotong karakter secara kasar bila memungkinkan."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts or ["(jawaban kosong)"]


def _load_offset() -> int:
    try:
        data = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
        return max(0, int(data.get("offset", 0)))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def _save_offset(offset: int) -> None:
    config.ensure_data_dirs()
    temporary = OFFSET_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"offset": offset}) + "\n", encoding="utf-8")
    temporary.replace(OFFSET_FILE)


def _allowed(chat_id: int, allowed: list[Any]) -> bool:
    """Allowlist kosong = public. Nilai disamakan sebagai string agar JSON aman."""
    return not allowed or str(chat_id) in {str(item) for item in allowed}


def _document_filename(document: dict[str, Any]) -> str:
    filename = str(document.get("file_name") or "").strip()
    return Path(filename).name or "file"


def _document_kind(filename: str, mime_type: str = "") -> str:
    suffix, mime = Path(filename).suffix.lower(), mime_type.lower()
    if suffix in SUPPORTED_TEXT_EXTENSIONS or mime.startswith("text/") or mime in {"application/json", "application/xml"}:
        return "text"
    if suffix == ".zip" or mime in {"application/zip", "application/x-zip-compressed"}:
        return "zip"
    if suffix in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "image"
    if suffix == ".pdf" or mime == "application/pdf":
        return "pdf"
    return "other"


def _safe_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\x00", " ").strip()


def _extract_document_text(filename: str, content: bytes, mime_type: str = "") -> tuple[str | None, str | None]:
    kind = _document_kind(filename, mime_type)
    if kind == "text":
        text = _safe_text(content)
        return (text, None) if text else (None, f"File `{filename}` kosong atau tidak berisi teks.")
    if kind == "image":
        return f"Image `{filename}` received ({len(content)} bytes). Use a vision-capable provider to inspect pixels.", None
    if kind == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages[:40]).strip()
            return (text[:MAX_ARCHIVE_TEXT_BYTES], None) if text else (None, f"PDF `{filename}` has no extractable text.")
        except Exception:
            return None, f"PDF `{filename}` cannot be read or is a scanned/image-only PDF."
    if kind != "zip":
        return None, f"File `{filename}` is not supported. Supported: text, JSON/CSV/code, ZIP, image metadata, and PDF notice."
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(members) > MAX_ARCHIVE_ENTRIES:
                return None, f"ZIP has too many files (limit {MAX_ARCHIVE_ENTRIES})."
            parts, consumed = [], 0
            for entry in members:
                path = Path(entry.filename)
                if path.is_absolute() or ".." in path.parts or _document_kind(path.name) != "text" or entry.file_size > MAX_ARCHIVE_TEXT_BYTES:
                    continue
                data = archive.read(entry)[:MAX_ARCHIVE_TEXT_BYTES - consumed]
                consumed += len(data)
                text = _safe_text(data)
                if text:
                    parts.append(f"--- {path.as_posix()} ---\n{text}")
                if consumed >= MAX_ARCHIVE_TEXT_BYTES:
                    break
            return ("\n\n".join(parts), None) if parts else (None, "ZIP has no safe text files to read.")
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return None, "ZIP is invalid or cannot be extracted."


def _download_document(api: str, token: str, document: dict[str, Any]) -> tuple[bytes | None, str | None]:
    filename, size = _document_filename(document), int(document.get("file_size") or 0)
    if size and size > TELEGRAM_TEXT_FILE_LIMIT:
        return None, f"File `{filename}` is too large (limit {TELEGRAM_TEXT_FILE_LIMIT} bytes)."
    meta = _api_call(api, "getFile", timeout=20, file_id=str(document.get("file_id") or ""))
    file_path = str((meta or {}).get("result", {}).get("file_path") or "")
    if not file_path:
        return None, f"Could not get Telegram metadata for `{filename}`."
    try:
        response = requests.get(FILE_API_TEMPLATE.format(token=token, file_path=file_path), timeout=30)
        if not response.ok or len(response.content) > TELEGRAM_TEXT_FILE_LIMIT:
            return None, f"Could not download `{filename}` safely."
        return response.content, None
    except requests.RequestException as exc:
        return None, f"Could not download `{filename}`: {exc.__class__.__name__}."


def _handle_command(text: str, sessions, identity: str, *, stop_event) -> str | None:
    command, _, args = text.partition(" ")
    command, args = command.split("@", 1)[0].lower(), args.strip()
    if command in {"/start", "/help"}:
        return "/status · /models · /model <id> · /new · /restart · /stop · /logs"
    if command == "/status":
        return f"Aesora active\nModel: `{config.MODEL}`\nProvider: `{config.BASE_URL}`\nSession: `{identity}`\nCached: {sessions.count()}"
    if command == "/models":
        return f"Current model: `{config.MODEL}`\nUse: /model provider/model-id"
    if command == "/model":
        if not args or len(args) > 200 or "\n" in args:
            return f"Current model: `{config.MODEL}`\nUse: /model provider/model-id"
        cfg = config.stored_config_copy()
        cfg["provider"]["model"] = args
        config.save_config(cfg)
        sessions.reset(identity)
        return f"Model updated: `{args}`. Your chat was reset."
    if command == "/new":
        sessions.reset(identity)
        return "New chat started."
    if command == "/restart":
        sessions.reset(identity)
        return "Your Telegram chat session was restarted."
    if command == "/stop":
        stop_event.set()
        return "Aesora gateway is stopping. Start it again from the installation terminal with `aesora start`."
    if command == "/logs":
        return "Check gateway logs from the installation terminal: `aesora logs`."
    return None


def _build_document_prompt(filename: str, file_text: str, caption: str = ""):
    header = f"User mengirim file `{filename}` lewat Telegram."
    if caption:
        header += f"\nCaption/perintah user: {caption}"
    return f"{header}\n\nIsi file:\n```\n{file_text}\n```"


def _send_agent_reply(api: str, sessions, *, chat_id: int, identity: str, text: str, tool_profile: str) -> None:
    _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")

    def on_tool(_name, _args):
        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")

    try:
        reply = sessions.send(
            identity=identity,
            text=text,
            tool_profile=tool_profile,
            on_tool=on_tool,
        )
    except AesoraError as exc:
        reply = f"Maaf, Aesora sedang bermasalah: {exc}"
    except Exception:
        print("  [telegram] unhandled agent error", flush=True)
        reply = "Maaf, terjadi error internal. Coba lagi sebentar."
    for part in _split_message(reply):
        _api_call(api, "sendMessage", chat_id=chat_id, text=part)


def start(sessions, cfg: dict[str, Any], stop_event) -> None:
    token = str(cfg["token"]).strip()
    api = API_TEMPLATE.format(token=token)
    allowed = cfg.get("allowed", [])
    tool_profile = str(cfg.get("tool_profile", "safe"))

    me = _api_call(api, "getMe", timeout=20)
    if not me:
        print("  [telegram] token tidak bisa diverifikasi; gateway dihentikan.", flush=True)
        return
    username = str((me.get("result") or {}).get("username", "?"))
    print(f"  [telegram] @{username} terhubung via polling", flush=True)

    offset = _load_offset()
    while not stop_event.is_set():
        try:
            response = requests.get(
                f"{api}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message"])},
                timeout=35,
            )
            payload = response.json()
            if not response.ok or not payload.get("ok"):
                description = str(payload.get("description", f"HTTP {response.status_code}"))[:160]
                print(f"  [telegram] getUpdates gagal: {description}", flush=True)
                stop_event.wait(3)
                continue
        except requests.Timeout:
            continue  # Long polling normal.
        except (requests.RequestException, ValueError) as exc:
            print(f"  [telegram] polling error: {exc.__class__.__name__}", flush=True)
            stop_event.wait(3)
            continue

        for update in payload.get("result", []):
            update_id = int(update.get("update_id", -1))
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            text = str(message.get("text") or "").strip()
            caption = str(message.get("caption") or "").strip()
            document = message.get("document") or {}

            try:
                if chat_id is None:
                    continue
                chat_id_int = int(chat_id)
                identity = f"telegram:{chat_id_int}"

                if not _allowed(chat_id_int, allowed):
                    _api_call(api, "sendMessage", chat_id=chat_id_int, text="Akses bot ini belum diizinkan.")
                    continue

                if text:
                    if text.startswith("/"):
                        command_reply = _handle_command(text, sessions, identity, stop_event=stop_event)
                        _api_call(api, "sendMessage", chat_id=chat_id_int, text=command_reply or "Unknown command. Use /help.")
                    else:
                        _send_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=text, tool_profile=tool_profile)
                elif document:
                    filename = _document_filename(document)
                    file_content, error = _download_document(api, token, document)
                    if error:
                        _api_call(api, "sendMessage", chat_id=chat_id_int, text=error)
                        continue
                    file_text, error = _extract_document_text(filename, file_content or b"", str(document.get("mime_type") or ""))
                    if error:
                        _api_call(api, "sendMessage", chat_id=chat_id_int, text=error)
                        continue
                    prompt = _build_document_prompt(filename, file_text or "", caption)
                    _send_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=prompt, tool_profile=tool_profile)
                elif caption:
                    _send_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=caption, tool_profile=tool_profile)
                else:
                    _api_call(
                        api,
                        "sendMessage",
                        chat_id=chat_id_int,
                        text="Pesan ini belum bisa diproses. Kirim teks biasa atau file .txt/.md.",
                    )
            finally:
                # Simpan setelah handler selesai agar restart tidak kehilangan update.
                offset = max(offset, update_id + 1)
                _save_offset(offset)

    print("  [telegram] berhenti", flush=True)
