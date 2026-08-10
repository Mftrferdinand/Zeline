"""Gateway Telegram Zeline via raw Bot API long polling.

Tidak memakai SDK Telegram agar instalasi tetap kecil dan mudah di Termux.
Setiap chat mendapat identity ``telegram:<chat_id>`` sendiri sehingga history
+ memory tidak pernah tercampur dengan user lain.
"""
from __future__ import annotations

import io
import html
import json
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

from zeline import config
from zeline.agent import ZelineError

API_TEMPLATE = "https://api.telegram.org/bot{token}"
FILE_API_TEMPLATE = "https://api.telegram.org/file/bot{token}/{file_path}"
OFFSET_FILE = config.STATE_DIR / "telegram-offset.json"
TELEGRAM_MESSAGE_LIMIT = 4_000
AGENT_INPUT_LIMIT = 16_000
# Batas unduhan dokumen Telegram. Isi ZIP/PDF tetap dibatasi terpisah saat diekstrak.
TELEGRAM_TEXT_FILE_LIMIT = 20 * 1024 * 1024
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".py", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".htm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_ARCHIVE_ENTRIES = 512
MAX_ARCHIVE_TEXT_BYTES = 256 * 1024


def _telegram_commands() -> list[dict[str, str]]:
    """Menu command ringkas seperti surface Telegram Hermes."""
    return [
        {"command": "start", "description": "Start Zeline"},
        {"command": "model", "description": "Switch model"},
        {"command": "status", "description": "Show runtime status"},
        {"command": "stop", "description": "Stop the active turn"},
        {"command": "new", "description": "Start a new session"},
        {"command": "steer", "description": "Steer the active turn"},
    ]


def _tool_names_for_profile(profile: str) -> list[str]:
    from zeline.tools import TOOL_DEFS
    return [definition.name for definition in TOOL_DEFS if profile in definition.profiles]


def _macos_terminal_card(command: str) -> str:
    """Render macOS-style terminal; Bash centered by exact character width."""
    lines = command.splitlines() or [""]
    longest = max((len(line) for line in lines), default=0)
    width = min(54, max(34, longest + 4))
    if width % 2:
        width += 1
    title = "Bash"
    left = (width - len(title)) // 2
    header = f"{'●  ●  ●':<{left}}{title}".ljust(width)
    rule = "─" * width
    body = []
    for index, line in enumerate(lines):
        prefix = "$ " if index == 0 else "> "
        available = max(1, width - len(prefix))
        while len(line) > available:
            body.append(prefix + line[:available])
            line = line[available:]
            prefix = "  "
            available = width - len(prefix)
        body.append(prefix + line)
    raw = "\n".join((rule, header, rule, *body, rule))
    return f"🖥️ Zeline Terminal\n<pre>{html.escape(raw, quote=False)}</pre>"


def _tool_progress_text(name: str, arguments: dict[str, Any]) -> str:
    """Render one distinct HTML-safe progress message per real tool call."""
    if name == "load_skill":
        return f"📚 Reading skill {html.escape(str(arguments.get('name', ''))[:100])}"
    if name == "run_shell":
        command = str(arguments.get("command", "")).strip()[:1500]
        return _macos_terminal_card(command)
    path = html.escape(str(arguments.get("path", ""))[:300], quote=False)
    if name == "read_file":
        return f"📖 Reading <code>{path}</code>"
    if name == "write_file":
        return f"✍️ Writing <code>{path}</code>"
    if name == "edit_file":
        return f"✏️ Editing <code>{path}</code>"
    if name == "patch_file":
        return f"🩹 Patching <code>{path}</code>"
    if name == "search_files":
        query = html.escape(str(arguments.get("query", ""))[:200], quote=False)
        return f"🔍 Searching files\n<code>{query}</code>"
    if name == "update_task":
        status = html.escape(str(arguments.get("status", "pending"))[:40], quote=False)
        task = html.escape(str(arguments.get("task", ""))[:300], quote=False)
        return f"📋 Updating tasks\n<code>{status}</code> · {task}"
    if name == "web_search":
        return f"🔎 Searching web: {html.escape(str(arguments.get('query', ''))[:200], quote=False)}"
    preview = html.escape(", ".join(f"{key}={str(value)[:80]}" for key, value in arguments.items()), quote=False)
    return f"🔧 {html.escape(name)}" + (f"\n<code>{preview}</code>" if preview else "")


def _working_status_text(elapsed_seconds: float) -> str:
    minutes = max(1, int(elapsed_seconds // 60))
    return f"⏳ Working — {minutes} min — waiting for provider response"


def _start_working_heartbeat(api: str, chat_id: int, done: threading.Event, *, interval: float = 60.0) -> threading.Thread:
    """Kirim status berkala tanpa menghalangi turn agent."""
    started = time.monotonic()

    def heartbeat() -> None:
        while not done.wait(interval):
            _api_call(api, "sendMessage", chat_id=chat_id, text=_working_status_text(time.monotonic() - started))

    worker = threading.Thread(target=heartbeat, name=f"zeline-heartbeat-{chat_id}", daemon=True)
    worker.start()
    return worker


def _model_picker_payload(models: list[str], current_model: str, provider_index: int | None = None, provider_name: str = "") -> tuple[str, dict[str, Any]]:
    """Bangun inline picker dengan callback pendek agar aman di batas 64 byte."""
    buttons = []
    for index, model in enumerate(models):
        label = model.rsplit("/", 1)[-1]
        if model == current_model:
            label = f"✓ {label}"
        callback = f"model:{index}" if provider_index is None else f"model:{provider_index}:{index}"
        buttons.append({"text": label[:48], "callback_data": callback})
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    if provider_index is not None:
        rows.append([{"text": "« Back", "callback_data": "provider:back"}])
    rows.append([{"text": "✗ Cancel", "callback_data": "model:cancel"}])
    return (
        (f"Select a model\nProvider: {provider_name}\nCurrent: {current_model or 'unknown'}" if provider_name else f"Select a model\nCurrent: {current_model or 'unknown'}"),
        {"inline_keyboard": rows},
    )


def _configured_providers() -> list[dict[str, str]]:
    cfg = config.stored_config_copy()
    stored = cfg.get("providers", {})
    providers: list[dict[str, str]] = []
    if isinstance(stored, dict):
        for slug, item in stored.items():
            if isinstance(item, dict) and item.get("base_url") and item.get("api_key"):
                providers.append({"slug": str(slug), **{key: str(value) for key, value in item.items()}})
    active = cfg.get("provider", {})
    active_slug = next((item["slug"] for item in providers if item.get("base_url") == str(active.get("base_url", "")) and item.get("name") == str(active.get("name", ""))), "")
    if not active_slug and isinstance(active, dict):
        name = str(active.get("name", "")).strip() or str(active.get("base_url", "provider")).split("://", 1)[-1].split("/", 1)[0]
        providers.insert(0, {"slug": "active", "name": name, **{key: str(value) for key, value in active.items()}})
    return providers


def _active_provider_slug(providers: list[dict[str, str]]) -> str:
    return next((item["slug"] for item in providers if item.get("base_url") == config.BASE_URL), providers[0]["slug"] if providers else "")


def _provider_picker_payload(providers: list[dict[str, str]], current_slug: str) -> tuple[str, dict[str, Any]]:
    buttons = []
    for index, provider in enumerate(providers):
        label = provider.get("name") or provider["slug"]
        if provider["slug"] == current_slug:
            label = f"✓ {label}"
        buttons.append({"text": label[:48], "callback_data": f"provider:{index}"})
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([{"text": "✗ Cancel", "callback_data": "model:cancel"}])
    return "Select a provider", {"inline_keyboard": rows}


def _discover_provider_models(provider: dict[str, str]) -> list[str]:
    try:
        response = requests.get(
            f"{provider.get('base_url', '').rstrip('/')}/models",
            headers={"Authorization": f"Bearer {provider.get('api_key', '')}"},
            timeout=20,
        )
        payload = response.json() if response.ok else {}
        models = [str(item.get("id", "")).strip() for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")]
        return list(dict.fromkeys(models)) or ([provider.get("model", "")] if provider.get("model") else [])
    except (requests.RequestException, ValueError):
        return [provider.get("model", "")] if provider.get("model") else []


def _discover_models() -> list[str]:
    """Ambil katalog model live dari provider OpenAI-compatible."""
    try:
        response = requests.get(
            f"{config.BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {config.API_KEY}"},
            timeout=20,
        )
        if not response.ok:
            return [config.MODEL] if config.MODEL else []
        payload = response.json()
        models = [
            str(item.get("id", "")).strip()
            for item in payload.get("data", [])
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ] if isinstance(payload, dict) else []
        return list(dict.fromkeys(models)) or ([config.MODEL] if config.MODEL else [])
    except (requests.RequestException, ValueError):
        return [config.MODEL] if config.MODEL else []


def _handle_command_update(
    api: str,
    text: str,
    sessions,
    identity: str,
    chat_id: int,
    *,
    stop_event,
    tool_profile: str,
) -> bool:
    """Handle command yang perlu payload Telegram selain teks biasa."""
    command, _, args = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    args = args.strip()
    if command == "/start":
        _api_call(
            api, "sendMessage", chat_id=chat_id,
            text=(
                f"Zeline is ready.\nModel: {config.MODEL}\n\n"
                "/model — switch model\n/status — show runtime status\n"
                "/stop — stop the active turn\n/new — start a new session\n"
                "/steer <prompt> — guide the active turn"
            ),
        )
        return True
    if command == "/status":
        status = sessions.status(identity)
        _api_call(
            api, "sendMessage", chat_id=chat_id,
            text=(
                "📊 Zeline Gateway Status\n\n"
                f"Session ID: {status['session_id']}\n"
                f"Title: {status['title']}\n"
                f"Created: {status['created']}\n"
                f"Last Activity: {status['last_activity']}\n"
                f"Model: {status['model']}\n"
                f"Context: {status['context']}\n"
                f"Agent Running: {'Yes' if status['agent_running'] else 'No'}\n\n"
                "Connected Platforms: Telegram"
            ),
        )
        return True
    if command == "/model" and not args.strip():
        providers = _configured_providers()
        picker_text, markup = _provider_picker_payload(providers, _active_provider_slug(providers))
        _api_call(api, "sendMessage", chat_id=chat_id, text=picker_text, reply_markup=markup)
        return True
    if command == "/stop":
        stopped = sessions.stop(identity)
        reply = "Stopped the active turn." if stopped else "No active turn to stop."
        _api_call(api, "sendMessage", chat_id=chat_id, text=reply)
        return True
    if command == "/new":
        sessions.stop(identity)
        sessions.reset(identity)
        _api_call(api, "sendMessage", chat_id=chat_id, text="New session started.")
        return True
    if command == "/steer":
        if not args:
            _api_call(api, "sendMessage", chat_id=chat_id, text="Usage: /steer <prompt>")
            return True
        if sessions.steer(identity, args):
            preview = args[:60] + ("..." if len(args) > 60 else "")
            _api_call(api, "sendMessage", chat_id=chat_id, text=f"⏩ Steer queued — arrives after the next tool call: '{preview}'")
        else:
            _start_agent_reply(
                api, sessions, chat_id=chat_id, identity=identity,
                text=args, tool_profile=tool_profile,
            )
        return True
    return False


def _handle_callback(api: str, callback: dict[str, Any], sessions) -> None:
    callback_id = str(callback.get("id", ""))
    data = str(callback.get("data", ""))
    message = callback.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id", 0))
    message_id = int(message.get("message_id", 0))
    _api_call(api, "answerCallbackQuery", callback_query_id=callback_id)
    if data == "model:cancel":
        _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text="Model selection cancelled.")
        return
    providers = _configured_providers()
    if data == "provider:back":
        picker_text, markup = _provider_picker_payload(providers, _active_provider_slug(providers))
        _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text=picker_text, reply_markup=markup)
        return
    if data.startswith("provider:"):
        try:
            provider_index = int(data.split(":", 1)[1])
            provider = providers[provider_index]
        except (ValueError, IndexError):
            _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text="Provider selection expired. Run /model again.")
            return
        models = _discover_provider_models(provider)
        picker_text, markup = _model_picker_payload(models, provider.get("model", ""), provider_index, provider.get("name", provider["slug"]))
        _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text=picker_text, reply_markup=markup)
        return
    if not data.startswith("model:"):
        return
    parts = data.split(":")
    try:
        if len(parts) == 3:
            provider = providers[int(parts[1])]
            models = _discover_provider_models(provider)
            selected = models[int(parts[2])]
        else:
            provider = None
            models = _discover_models()
            selected = models[int(parts[1])]
    except (ValueError, IndexError):
        _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text="Model selection expired. Run /model again.")
        return
    cfg = config.stored_config_copy()
    if provider is not None:
        chosen = {key: value for key, value in provider.items() if key != "slug"}
        chosen["model"] = selected
        cfg["provider"] = chosen
        cfg.setdefault("providers", {})[provider["slug"]] = chosen
    else:
        cfg["provider"]["model"] = selected
    config.save_config(cfg)
    sessions.reset(f"telegram:{chat_id}")
    provider_line = f"\nProvider: {provider.get('name', provider['slug'])}" if provider else ""
    _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text=f"Model switched to: {selected}{provider_line}\nSaved globally. New session started.")


_FENCED_CODE_RE = re.compile(r"```([A-Za-z0-9_+.-]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


def _markdown_to_telegram_html(text: str) -> str:
    """Render subset Markdown ke Telegram HTML tanpa mengizinkan HTML mentah."""
    code_blocks: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        language = re.sub(r"[^A-Za-z0-9_+.-]", "", match.group(1))
        code = html.escape(match.group(2).rstrip("\n"), quote=False)
        language_attr = f' class="language-{language}"' if language else ""
        code_blocks.append(f"<pre><code{language_attr}>{code}</code></pre>")
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    rendered = _FENCED_CODE_RE.sub(keep_code, text)
    rendered = html.escape(rendered, quote=False)
    rendered = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", rendered)
    rendered = _LINK_RE.sub(r'<a href="\2">\1</a>', rendered)
    rendered = _INLINE_CODE_RE.sub(r"<code>\1</code>", rendered)
    rendered = _BOLD_RE.sub(r"<b>\1</b>", rendered)
    for index, block in enumerate(code_blocks):
        rendered = rendered.replace(f"\x00CODE{index}\x00", block)
    return rendered


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
    if profile == "full" and not allowed:
        return ["tool_profile full membutuhkan owner allowlist Telegram"]
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
            if not parts:
                return None, "ZIP has no safe text files to read."
            return "\n\n".join(parts)[:16_000], None
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
        return f"Zeline active\nModel: `{config.MODEL}`\nProvider: `{config.BASE_URL}`\nSession: `{identity}`\nCached: {sessions.count()}"
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
        return "Zeline gateway is stopping. Start it again from the installation terminal with `zeline start`."
    if command == "/logs":
        return "Check gateway logs from the installation terminal: `zeline logs`."
    return None


def _build_document_prompt(filename: str, file_text: str, caption: str = "") -> str:
    """Bangun input agen tanpa melampaui batas 16.000 karakter agen."""
    header = f"User mengirim file `{filename}` lewat Telegram."
    if caption:
        header += f"\nCaption/perintah user: {caption}"
    prefix = f"{header}\n\nIsi file:\n```\n"
    suffix = "\n```"
    available = max(0, AGENT_INPUT_LIMIT - len(prefix) - len(suffix))
    return f"{prefix}{file_text[:available]}{suffix}"


def _send_agent_reply(api: str, sessions, *, chat_id: int, identity: str, text: str, tool_profile: str) -> None:
    _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")
    done = threading.Event()
    heartbeat = _start_working_heartbeat(api, chat_id, done)

    def on_tool(_name, _args):
        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")
        _api_call(
            api, "sendMessage", chat_id=chat_id,
            text=_tool_progress_text(_name, _args), parse_mode="HTML",
        )

    try:
        reply = sessions.send(
            identity=identity,
            text=text,
            tool_profile=tool_profile,
            on_tool=on_tool,
        )
    except ZelineError as exc:
        reply = f"Maaf, Zeline sedang bermasalah: {exc}"
    except Exception:
        print("  [telegram] unhandled agent error", flush=True)
        reply = "Maaf, terjadi error internal. Coba lagi sebentar."
    finally:
        done.set()
        heartbeat.join(timeout=0.2)
    for part in _split_message(reply):
        _api_call(
            api,
            "sendMessage",
            chat_id=chat_id,
            text=_markdown_to_telegram_html(part),
            parse_mode="HTML",
        )


def _start_agent_reply(api: str, sessions, *, chat_id: int, identity: str, text: str, tool_profile: str) -> threading.Thread:
    """Jalankan turn di worker agar polling tetap menerima /stop dan /steer."""
    worker = threading.Thread(
        target=_send_agent_reply,
        kwargs={
            "api": api,
            "sessions": sessions,
            "chat_id": chat_id,
            "identity": identity,
            "text": text,
            "tool_profile": tool_profile,
        },
        name=f"zeline-telegram-{chat_id}",
        daemon=True,
    )
    worker.start()
    return worker


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
    _api_call(api, "setMyCommands", commands=_telegram_commands())

    offset = _load_offset()
    while not stop_event.is_set():
        try:
            response = requests.get(
                f"{api}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])},
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
            callback = update.get("callback_query") or {}
            message = update.get("message") or {}
            if callback:
                callback_message = callback.get("message") or {}
                callback_chat_id = (callback_message.get("chat") or {}).get("id")
                callback_user_id = (callback.get("from") or {}).get("id")
                try:
                    if callback_chat_id is not None and callback_user_id is not None and _allowed(int(callback_user_id), allowed):
                        _handle_callback(api, callback, sessions)
                    elif callback.get("id"):
                        _api_call(api, "answerCallbackQuery", callback_query_id=str(callback["id"]), text="Access denied.", show_alert=True)
                finally:
                    offset = max(offset, update_id + 1)
                    _save_offset(offset)
                continue
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
                        handled = _handle_command_update(
                            api, text, sessions, identity, chat_id_int,
                            stop_event=stop_event, tool_profile=tool_profile,
                        )
                        if not handled:
                            command_reply = _handle_command(text, sessions, identity, stop_event=stop_event)
                            _api_call(api, "sendMessage", chat_id=chat_id_int, text=command_reply or "Unknown command. Use /start.")
                    else:
                        _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=text, tool_profile=tool_profile)
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
                    _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=prompt, tool_profile=tool_profile)
                elif caption:
                    _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=caption, tool_profile=tool_profile)
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
