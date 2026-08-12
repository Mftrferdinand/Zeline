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
from urllib.parse import urlparse

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
REPOSITORY_FILE = config.DATA_DIR / "repository.md"
REPOSITORY_HEADER = "## Repository Archive\n\n| # | Repository | Link |\n|---|------------|------|\n"
_URL_RE = re.compile(r"https?://[^\s<>\])}]+")


def _telegram_commands() -> list[dict[str, str]]:
    """Menu command ringkas seperti surface Telegram Hermes."""
    return [
        {"command": "start", "description": "Start Zeline"},
        {"command": "model", "description": "Switch model"},
        {"command": "status", "description": "Show runtime status"},
        {"command": "repository", "description": "Download repository archive"},
        {"command": "savetask", "description": "Save the current task"},
        {"command": "updatetask", "description": "Update task by project or link"},
        {"command": "completedtask", "description": "Mark task as finished"},
        {"command": "deletetask", "description": "Delete task by project or link"},
        {"command": "stop", "description": "Stop the active turn"},
        {"command": "new", "description": "Start a new session"},
        {"command": "steer", "description": "Steer the active turn"},
    ]


def _tool_names_for_profile(profile: str) -> list[str]:
    from zeline.tools import TOOL_DEFS
    return [definition.name for definition in TOOL_DEFS if profile in definition.profiles]


def _terminal_progress(command: str, *, search: bool = False) -> str:
    """Preview terminal.

    Pencarian (web/skill/lokal): blok terminal biasa (monospace), tanpa emoji
    dan tanpa judul 'Zeline Terminal' — command langsung di dalam blok. Untuk
    coding: blok terminal penuh dengan judul.
    """
    escaped = html.escape(command.strip()[:1500], quote=False)
    if search:
        return f"<pre>{escaped}</pre>"
    return f"🖥️ Zeline Terminal\n<pre>{escaped}</pre>"


def _is_search_command(command: str) -> bool:
    """True bila perintah shell bertujuan pencarian/riset informasi."""
    low = command.lower()
    return any(k in low for k in ("search", "researching", "curl ", "jina.ai", "duckduckgo", "google.com/search"))


def _tool_progress_text(name: str, arguments: dict[str, Any]) -> str:
    """Render one distinct HTML-safe progress message per real tool call.

    Fase kerja (present-progressive): 🔎 Searching / 🔍 Researching.
    Tidak pernah menampilkan URL/link mentah ke user — cukup topik/kueri.
    """
    if name == "load_skill":
        return f"📚 Reading skill {html.escape(str(arguments.get('name', ''))[:100])}"
    if name == "run_shell":
        command = str(arguments.get("command", ""))
        return _terminal_progress(command, search=_is_search_command(command))
    if name == "execute_code":
        code = str(arguments.get("code", "")).strip()
        first = html.escape((code.splitlines() or ["code"])[0][:100], quote=False)
        return f"🐍 Running code from <code>{first}</code>..."
    if name == "update_skill":
        skill_name = html.escape(str(arguments.get("name", ""))[:100], quote=False)
        return f"📝 Updating skill <code>{skill_name}</code>"
    if name == "save_skill":
        skill_name = html.escape(str(arguments.get("name", ""))[:100], quote=False)
        return f"💡 Saving skill <code>{skill_name}</code>"
    path = html.escape(str(arguments.get("path", ""))[:300], quote=False)
    if name == "read_file":
        offset = max(1, int(arguments.get("offset", 1) or 1))
        limit = max(1, int(arguments.get("limit", 500) or 500))
        return f"📖 Reading <code>{path}</code> L{offset}-{offset + limit - 1}"
    if name == "write_file":
        return f"✍️ Writing <code>{path}</code>"
    if name == "edit_file":
        return f"✏️ Editing <code>{path}</code>"
    if name == "patch_file":
        return f"🩹 Patching <code>{path}</code>"
    if name == "search_files":
        query = html.escape(str(arguments.get("query", ""))[:200], quote=False)
        return f"🔎 Searching {query}"
    if name == "update_task":
        status = html.escape(str(arguments.get("status", "pending"))[:40], quote=False)
        task = html.escape(str(arguments.get("task", ""))[:300], quote=False)
        return f"📋 Updating tasks\n<code>{status}</code> · {task}"
    if name == "web_search":
        # Searching hanya penanda ringkas: subjek utama (kata pertama) + '…'.
        # Detail lengkap muncul di baris Researching, bukan di sini.
        query = str(arguments.get("query", "")).strip()
        subject = html.escape((query.split() or [""])[0][:40], quote=False)
        return f"🔎 Searching {subject}…" if subject else "🔎 Searching…"
    if name == "web_fetch":
        # Baca sumber web tidak ditampilkan sebagai baris terpisah (biar bersih).
        return ""
    if name == "deep_research":
        return f"🔍 Researching {html.escape(str(arguments.get('query', ''))[:100], quote=False)}"
    preview = html.escape(", ".join(f"{key}={str(value)[:80]}" for key, value in arguments.items()), quote=False)
    return f"🔧 {html.escape(name)}" + (f"\n<code>{preview}</code>" if preview else "")


def _progress_category(line: str) -> str | None:
    """Kategori baris feed untuk collapse. None = baris unik (jangan digabung)."""
    if line.startswith("📚"):
        return "skill"
    if line.startswith("🔎"):
        return "search"
    if line.startswith("🔍"):
        return "research"
    if line.startswith("📖"):
        return "read"
    return None


# Urutan tampilan tetap agar feed rapi & logis, apa pun urutan model memanggil
# tool: baca skill → searching → researching → membaca hasil → lainnya.
_CATEGORY_ORDER = {"skill": 0, "search": 1, "research": 2, "read": 3}


def _ordered_lines(lines: list[str]) -> list[str]:
    """Urutkan baris feed berdasarkan kategori tetap, stabil untuk kategori lain."""
    def key(item: tuple[int, str]) -> tuple[int, int]:
        index, line = item
        category = _progress_category(line)
        rank = _CATEGORY_ORDER.get(category, 99) if category else 99
        return (rank, index)
    return [line for _index, line in sorted(enumerate(lines), key=key)]


def _finalize_line(line: str) -> str:
    """Ubah baris fase-kerja menjadi bentuk 'selesai' — semua jadi '📖 Reading'."""
    replacements = (
        ("🔎 Searching", "📖 Reading"),
        ("🔍 Researching", "📖 Reading"),
    )
    for old, new in replacements:
        if line.startswith(old):
            return new + line[len(old):]
    return line


def _tool_result_text(name: str, arguments: dict[str, Any], result: str) -> str | None:
    """Render hanya hasil nyata yang bernilai sebagai progress terpisah."""
    if name in {"update_skill", "save_skill"} and not result.startswith("ERROR"):
        return f"📒 Self-improvement: {html.escape(result[:1000], quote=False)}"
    return None


def _working_status_text(elapsed_seconds: float, *, iteration: int | None = None, maximum: int | None = None) -> str:
    """Header status live. Delay dilaporkan sebagai provider lambat (dari elapsed
    monotonic nyata), bukan klaim bahwa agent sedang sibuk."""
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)
    clock = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
    slow = " · provider is slow to respond" if elapsed_seconds >= 120 else ""
    return f"⏳ Working — {clock}{slow}"


def _provider_wait_text(wait_seconds: float, model: str = "") -> str:
    """Header khusus fase menunggu respons provider (LLM sedang berpikir).

    Hanya ini yang boleh menyebut 'Working/menunggu model' — bukan saat tool
    (Reading/Searching) sedang jalan, karena itu bagian cepat.
    """
    who = f" {model}" if model else ""
    detail = " (provider slow/overloaded, or model is thinking)" if wait_seconds >= 30 else ""
    return f"⏳ Waiting{who} — {int(wait_seconds)}s with no response{detail}"


class _LiveStatus:
    """Satu pesan Telegram yang di-edit berulang (bukan spam pesan baru).

    Dua fase:
      - ``waiting``: menunggu respons LLM → header 'Menunggu model — Ns'.
      - ``tool``: sedang menjalankan tool → hanya feed aktivitas bersih,
        tanpa label 'Working' (bagian ini cepat, bukan sumber delay).
    Aman dipakai dari worker heartbeat dan callback tool (dilindungi lock).
    """

    def __init__(self, api: str, chat_id: int, *, max_lines: int = 6, model: str = ""):
        self.api = api
        self.chat_id = chat_id
        self.max_lines = max_lines
        self.model = model
        self.message_id: int | None = None
        self.lines: list[str] = []
        self.phase = "waiting"
        self.phase_started = time.monotonic()
        self._last_text: str | None = None
        self._lock = threading.Lock()

    def _render(self) -> str:
        ordered = _ordered_lines(self.lines)[-self.max_lines:]
        feed = ("\n" + "\n".join(ordered)) if ordered else ""
        # Header konsisten '⏳ Processing...'. Delay panjang diberi catatan provider.
        if self.phase == "waiting":
            wait = time.monotonic() - self.phase_started
            if wait >= 30 and self.model:
                header = f"⏳ Processing… ({self.model} is slow to respond)"
            else:
                header = "⏳ Processing..."
        else:
            header = "⏳ Processing..."
        return header + feed

    def _push_locked(self, force: bool = False, allow_create: bool = True) -> None:
        # Bubble progres HANYA dibuat saat ada aktivitas tool nyata (search/coding/
        # fetch). Selama sekadar menunggu respons LLM, jangan pernah membuat bubble
        # baru — indikator 'typing…' native Telegram sudah cukup. Ini mencegah
        # (a) 'Processing' muncul di pertanyaan ringan tanpa tool, dan
        # (b) bubble muncul lalu hilang ketika finalize tidak menemukan aktivitas.
        if self.message_id is None and not allow_create:
            return
        text = self._render()
        if text == self._last_text and not force:
            return
        self._last_text = text
        if self.message_id is None:
            payload = _api_call(
                self.api, "sendMessage", chat_id=self.chat_id,
                text=text, parse_mode="HTML",
            )
            if payload and isinstance(payload.get("result"), dict):
                self.message_id = payload["result"].get("message_id")
        else:
            _api_call(
                self.api, "editMessageText", chat_id=self.chat_id,
                message_id=self.message_id, text=text, parse_mode="HTML",
            )

    def set_waiting(self) -> None:
        """Tandai bahwa kita sedang menunggu respons provider (LLM berpikir).

        Fase menunggu TIDAK pernah membuat bubble baru — kalau bubble sudah ada
        (karena tool sempat jalan), header-nya di-refresh; kalau belum ada,
        dibiarkan kosong supaya pertanyaan ringan tanpa tool tidak memunculkan
        'Processing'.
        """
        with self._lock:
            self.phase = "waiting"
            self.phase_started = time.monotonic()
            self._push_locked(allow_create=False)

    def add(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        with self._lock:
            self.phase = "tool"
            self.phase_started = time.monotonic()
            category = _progress_category(line)
            if category is not None:
                # Collapse: cukup SATU baris per kategori (mis. semua Searching
                # digabung jadi satu baris terbaru), biar feed tidak menumpuk.
                for index, existing in enumerate(self.lines):
                    if _progress_category(existing) == category:
                        self.lines[index] = line
                        break
                else:
                    self.lines.append(line)
            elif not self.lines or self.lines[-1] != line:
                self.lines.append(line)
            # Ada aktivitas tool nyata → di sinilah bubble progres boleh dibuat.
            self._push_locked(allow_create=True)

    def tick(self) -> None:
        # Heartbeat hanya me-refresh bubble yang SUDAH ada; tidak pernah membuat
        # bubble baru saat cuma menunggu LLM.
        with self._lock:
            self._push_locked(allow_create=False)

    def finalize(self) -> None:
        """Kunci bubble progres sebagai catatan permanen 'apa yang dikerjakan'.

        Header berubah jadi '⏳ Successful' dan baris fase-kerja jadi bentuk
        selesai (Searching→Reading). Kalau tidak ada aktivitas tool sama sekali
        (jawaban langsung), bubble dihapus agar tidak menyisakan pesan kosong.
        """
        with self._lock:
            if self.message_id is None:
                return
            if not self.lines:
                _api_call(
                    self.api, "deleteMessage",
                    chat_id=self.chat_id, message_id=self.message_id,
                )
                self.message_id = None
                return
            body = "\n".join(_finalize_line(line) for line in _ordered_lines(self.lines)[-self.max_lines:])
            final = f"⏳ Successful\n{body}"
            _api_call(
                self.api, "editMessageText", chat_id=self.chat_id,
                message_id=self.message_id, text=final, parse_mode="HTML",
            )

    def clear(self) -> None:
        """Hapus pesan status. Dipakai bila turn dibatalkan/error tanpa hasil."""
        with self._lock:
            if self.message_id is not None:
                _api_call(
                    self.api, "deleteMessage",
                    chat_id=self.chat_id, message_id=self.message_id,
                )
                self.message_id = None


def _start_working_heartbeat(
    api: str,
    chat_id: int,
    done: threading.Event,
    *,
    interval: float = 4.0,
    status: "_LiveStatus | None" = None,
) -> threading.Thread:
    """Jaga indikator 'typing…' tetap hidup selama agent bekerja.

    Telegram hanya menampilkan 'typing…' ~5 detik per ``sendChatAction``. Kalau
    model berpikir lama (atau load skill + reasoning), tanpa refresh indikator
    hilang dan bot tampak diam/polos lalu tiba-tiba mengirim jawaban. Maka tiap
    tick (default 4s < 5s) kita:
      1) kirim ulang ``sendChatAction typing`` agar 'sedang mengetik' terus tampil,
      2) ``tick()`` bubble progres yang SUDAH ada (tanpa membuat yang baru).
    """
    live = status or _LiveStatus(api, chat_id)

    def heartbeat() -> None:
        while not done.wait(interval):
            try:
                _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")
            except Exception:
                pass
            live.tick()

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


def _provider_label() -> str:
    """Nama provider aman untuk status; tidak pernah menampilkan credential."""
    provider = config.stored_config_copy().get("provider", {})
    name = str(provider.get("name", "")).strip()
    return name or (urlparse(config.BASE_URL).hostname or "unknown")


def _new_session_text() -> str:
    return (
        "🌟 Session reset! Starting fresh.\n"
        f"✦ Model : {config.MODEL}\n"
        f"✦ Provider : {_provider_label()}\n"
        "✦ Context : 0 tokens\n"
        f"✦ Endpoint : {config.BASE_URL}\n"
        "✦ Tip : Use /status to check this session."
    )


def _ensure_repository() -> Path:
    REPOSITORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not REPOSITORY_FILE.exists():
        REPOSITORY_FILE.write_text(REPOSITORY_HEADER, encoding="utf-8")
    return REPOSITORY_FILE


def _short_task_title(raw: str) -> str:
    clean = re.sub(r"\s+", " ", raw).strip(" .-|#")
    if not clean or clean.lower() == "new session":
        return "Untitled Task"
    return " ".join(clean.split()[:4]).title()[:60].replace("|", "-")


def _task_link(messages: list[str], chat_id: int, message_id: int) -> tuple[str, str]:
    for text in reversed(messages):
        match = _URL_RE.search(text)
        if match:
            url = match.group(0).rstrip(".,;:!?")
            return urlparse(url).netloc or url, url
    return "Telegram message", f"tg://openmessage?user_id={chat_id}&message_id={message_id}"


def _task_row(snapshot: dict[str, object], chat_id: int, message_id: int) -> str:
    title = _short_task_title(str(snapshot.get("title") or ""))
    raw_messages = snapshot.get("messages", [])
    messages = [str(item) for item in raw_messages] if isinstance(raw_messages, list) else []
    label, url = _task_link(messages, chat_id, message_id)
    return f"| 🟡 | {title} | [{label}]({url}) |\n"


def _repository_rows() -> tuple[Path, list[str]]:
    path = _ensure_repository()
    rows = [line + "\n" for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("| 🟡 |") or line.startswith("| 🟢 |")]
    return path, rows


def _write_repository_rows(path: Path, rows: list[str]) -> None:
    path.write_text(REPOSITORY_HEADER + "".join(rows), encoding="utf-8")


def _matching_row_indexes(rows: list[str], query: str) -> list[int]:
    needle = query.casefold().strip()
    return [index for index, row in enumerate(rows) if needle and needle in row.casefold()]


def _save_task(snapshot: dict[str, object] | None, chat_id: int, message_id: int) -> str:
    if not snapshot:
        return "no_task"
    path, rows = _repository_rows()
    row = _task_row(snapshot, chat_id, message_id)
    # Save hanya untuk entri pertama: nama atau URL yang sama dianggap duplikat.
    title = row.split("|", 4)[2].strip().casefold()
    url_match = re.search(r"\]\(([^)]+)\)", row)
    url = url_match.group(1).casefold() if url_match else ""
    if any(title in existing.casefold() or (url and url in existing.casefold()) for existing in rows):
        return "duplicate"
    rows.append(row)
    _write_repository_rows(path, rows)
    return "saved"


def _update_task(query: str, snapshot: dict[str, object] | None, chat_id: int, message_id: int) -> str:
    if not snapshot:
        return "no_task"
    path, rows = _repository_rows()
    matches = _matching_row_indexes(rows, query)
    if not matches:
        return "not_found"
    if len(matches) > 1:
        return "ambiguous"
    rows[matches[0]] = _task_row(snapshot, chat_id, message_id)
    _write_repository_rows(path, rows)
    return "updated"


def _delete_task(query: str) -> str:
    path, rows = _repository_rows()
    matches = _matching_row_indexes(rows, query)
    if not matches:
        return "not_found"
    if len(matches) > 1:
        return "ambiguous"
    rows.pop(matches[0])
    _write_repository_rows(path, rows)
    return "deleted"


def _complete_task(query: str) -> str:
    path, rows = _repository_rows()
    matches = _matching_row_indexes(rows, query)
    if not matches:
        return "not_found"
    if len(matches) > 1:
        return "ambiguous"
    index = matches[0]
    if rows[index].startswith("| 🟢 |"):
        return "already_completed"
    rows[index] = rows[index].replace("| 🟡 |", "| 🟢 |", 1)
    _write_repository_rows(path, rows)
    return "completed"


def _handle_command_update(
    api: str,
    text: str,
    sessions,
    identity: str,
    chat_id: int,
    *,
    stop_event,
    tool_profile: str,
    message_id: int = 0,
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
                "╭─ <b>Zeline Gateway Status</b>\n"
                f"├ Session ID : <code>{html.escape(str(status['session_id']))}</code>\n"
                f"├ Provider : <code>{html.escape(_provider_label())}</code>\n"
                f"├ Model : <code>{html.escape(str(status['model']))}</code>\n"
                f"├ Title : {html.escape(str(status['title']))}\n"
                f"├ Context : {html.escape(str(status['context']))}\n"
                f"├ Agent Running : {'Yes' if status['agent_running'] else 'No'}\n"
                "├ Platform : Telegram\n"
                "╰───────────────"
            ), parse_mode="HTML",
        )
        return True
    if command == "/repository":
        repository = _ensure_repository()
        if not _send_document(api, chat_id, repository):
            _api_call(api, "sendMessage", chat_id=chat_id, text="Gagal mengirim repository archive.")
        return True
    if command == "/savetask":
        snapshot = sessions.task_snapshot(identity)
        result = _save_task(snapshot, chat_id, message_id)
        reply = {
            "saved": "Task saved to repository.md.",
            "duplicate": "Task already exists. Use /updatetask <project or link>.",
            "no_task": "No task to save. Start a task first.",
        }[result]
        _api_call(api, "sendMessage", chat_id=chat_id, text=reply)
        return True
    if command == "/updatetask":
        if not args:
            reply = "Usage: /updatetask <project or link>"
        else:
            result = _update_task(args, sessions.task_snapshot(identity), chat_id, message_id)
            reply = {
                "updated": "Task updated in repository.md.",
                "not_found": "Task not found. Mention its project name or link.",
                "ambiguous": "Multiple tasks matched. Use a more specific project name or link.",
                "no_task": "No current task data available for the update.",
            }[result]
        _api_call(api, "sendMessage", chat_id=chat_id, text=reply)
        return True
    if command == "/completedtask":
        if not args:
            reply = "Usage: /completedtask <project or link>"
        else:
            result = _complete_task(args)
            reply = {
                "completed": "Task marked as finished in repository.md.",
                "already_completed": "Task is already marked as finished.",
                "not_found": "Task not found. Mention its project name or link.",
                "ambiguous": "Multiple tasks matched. Use a more specific project name or link.",
            }[result]
        _api_call(api, "sendMessage", chat_id=chat_id, text=reply)
        return True
    if command == "/deletetask":
        if not args:
            reply = "Usage: /deletetask <project or link>"
        else:
            result = _delete_task(args)
            reply = {
                "deleted": "Task deleted from repository.md.",
                "not_found": "Task not found. Mention its project name or link.",
                "ambiguous": "Multiple tasks matched. Use a more specific project name or link.",
            }[result]
        _api_call(api, "sendMessage", chat_id=chat_id, text=reply)
        return True
    if command == "/model" and not args.strip():
        providers = _configured_providers()
        picker_text, markup = _provider_picker_payload(providers, _active_provider_slug(providers))
        _api_call(api, "sendMessage", chat_id=chat_id, text=picker_text, reply_markup=markup)
        return True
    if command == "/stop":
        status = sessions.status(identity)
        stopped = sessions.stop(identity)
        title = str(status.get("title") or "Active task").strip()
        reply = f"❄️ {title}" if stopped else "No active task to stop."
        _api_call(api, "sendMessage", chat_id=chat_id, text=reply)
        return True
    if command == "/new":
        sessions.stop(identity)
        # Self-improvement review sebelum konteks dibuang: sesi berbobot bisa
        # menyimpan/memperbaiki skill. Best-effort, tidak menghalangi reset.
        try:
            summary = sessions.reflect(identity)
        except Exception:
            summary = None
        if summary:
            _api_call(
                api, "sendMessage", chat_id=chat_id,
                text=f"📒 Self-improvement:\n{html.escape(summary[:1500], quote=False)}",
                parse_mode="HTML",
            )
        sessions.reset(identity)
        _api_call(api, "sendMessage", chat_id=chat_id, text=_new_session_text())
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
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BULLET_RE = re.compile(r"(?m)^(\s*)[\*\+•·–—]\s+(?=\S)")
_ORDERED_RE = re.compile(r"(?m)^(\s*)(\d+)[.)]\s+(?=\S)")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _split_table_row(line: str) -> list[str]:
    """Pecah satu baris tabel markdown jadi sel-sel (buang pipe tepi & spasi)."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _convert_tables(text: str) -> str:
    """Ubah tabel Markdown (yang tidak dirender Telegram) jadi daftar berlabel.

    Telegram TIDAK punya tabel; baris `| a | b |` muncul mentah dan terlihat
    berantakan. Tiap baris data diubah jadi blok: sel pertama sebagai judul tebal,
    sisanya sebagai `Header: nilai` — rapi & enak dibaca di mobile.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Kandidat tabel: baris ber-pipe diikuti baris separator (---|---).
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            headers = _split_table_row(line)
            j = i + 2
            rows: list[list[str]] = []
            while j < n and "|" in lines[j] and lines[j].strip():
                rows.append(_split_table_row(lines[j]))
                j += 1
            # Render tiap baris data sebagai blok berlabel.
            for row in rows:
                # Judul blok = sel pertama (kalau ada isinya).
                title = row[0] if row else ""
                if title:
                    out.append(f"**{title}**")
                for col in range(1, len(headers)):
                    head = headers[col] if col < len(headers) else ""
                    val = row[col] if col < len(row) else ""
                    if head or val:
                        out.append(f"- {head}: {val}" if head else f"- {val}")
                out.append("")  # pemisah antar baris
            if rows:
                if out and out[-1] == "":
                    out.pop()
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _normalize_markdown(text: str) -> str:
    """Rapikan output model sebelum render: buang spasi/newline berantakan,
    seragamkan bullet, dan pertahankan blok kode apa adanya.

    Model kecil sering mengembalikan trailing space, baris kosong beruntun,
    bullet campur (``*``/``•``/``–``), dan heading tanpa spasi. Normalisasi ini
    membuat pesan Telegram rapi tanpa mengubah makna atau isi kode.
    """
    if not text:
        return text

    # 1) Amankan fenced code agar tidak ikut dinormalisasi.
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00BLOCK{len(protected) - 1}\x00"

    working = _FENCED_CODE_RE.sub(_protect, text)

    # 0) Ubah tabel Markdown → daftar berlabel (Telegram tidak punya tabel).
    working = _convert_tables(working)

    lines = []
    for raw in working.splitlines():
        line = raw.rstrip()  # buang trailing whitespace
        # Seragamkan penanda bullet menjadi "- " (indent dipertahankan).
        line = _BULLET_RE.sub(lambda m: f"{m.group(1)}- ", line)
        lines.append(line)
    working = "\n".join(lines)

    # 2) Rapatkan >=3 newline menjadi maksimal satu baris kosong.
    working = re.sub(r"\n{3,}", "\n\n", working)

    # 3) Beri satu baris kosong sebelum heading agar mudah dipindai.
    working = re.sub(r"(?<=\S)\n(#{1,6}\s)", r"\n\n\1", working)

    # 4) Buang spasi ganda di dalam prose (bukan di awal baris/indent).
    working = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", working)

    # 5) Kembalikan blok kode.
    for index, block in enumerate(protected):
        working = working.replace(f"\x00BLOCK{index}\x00", block)

    return working.strip()


def _markdown_to_telegram_html(text: str) -> str:
    """Render subset Markdown ke Telegram HTML tanpa mengizinkan HTML mentah."""
    text = _normalize_markdown(text)
    code_blocks: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        language = re.sub(r"[^A-Za-z0-9_+.-]", "", match.group(1))
        code = html.escape(match.group(2).rstrip("\n"), quote=False)
        language_attr = f' class="language-{language}"' if language else ""
        code_blocks.append(f"<pre><code{language_attr}>{code}</code></pre>")
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    rendered = _FENCED_CODE_RE.sub(keep_code, text)
    rendered = html.escape(rendered, quote=False)
    # Heading → bold line (judul otomatis tebal).
    rendered = _HEADING_RE.sub(r"<b>\1</b>", rendered)
    rendered = _LINK_RE.sub(r'<a href="\2">\1</a>', rendered)
    rendered = _INLINE_CODE_RE.sub(r"<code>\1</code>", rendered)
    rendered = _BOLD_RE.sub(r"<b>\1</b>", rendered)
    rendered = _STRIKE_RE.sub(r"<s>\1</s>", rendered)
    rendered = _ITALIC_RE.sub(r"<i>\1</i>", rendered)
    # Bullet "- " → "• " agar terlihat sebagai poin rapi di Telegram.
    rendered = re.sub(r"(?m)^(\s*)- (?=\S)", r"\1• ", rendered)
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


def _send_document(api: str, chat_id: int, path: Path) -> bool:
    """Kirim dokumen Telegram tanpa caption atau pesan teks kedua."""
    try:
        with path.open("rb") as document:
            response = requests.post(
                f"{api}/sendDocument",
                data={"chat_id": str(chat_id)},
                files={"document": (path.name, document, "text/markdown")},
                timeout=65,
            )
        payload = response.json()
        return bool(response.ok and payload.get("ok"))
    except (OSError, requests.RequestException, ValueError):
        return False


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
    live = _LiveStatus(api, chat_id, model=getattr(config, "MODEL", ""))
    heartbeat = _start_working_heartbeat(api, chat_id, done, status=live)

    def on_tool(_name, _args):
        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")
        # Tambahkan satu baris ringkas ke feed live, bukan pesan baru.
        line = _tool_progress_text(_name, _args).replace("\n", " ")[:200]
        live.add(line)

    def on_tool_result(_name, _args, result):
        result_text = _tool_result_text(_name, _args, result)
        if result_text:
            live.add(result_text.replace("\n", " ")[:200])
        # Setelah tool selesai, kita kembali menunggu respons model.
        live.set_waiting()

    def on_iteration(current, maximum):
        # Awal tiap iterasi = mulai menunggu respons provider.
        live.set_waiting()

    ok = False
    try:
        reply = sessions.send(
            identity=identity,
            text=text,
            tool_profile=tool_profile,
            on_tool=on_tool,
            on_tool_result=on_tool_result,
            on_iteration=on_iteration,
        )
        ok = True
    except ZelineError as exc:
        reply = f"Maaf, Zeline sedang bermasalah: {exc}"
    except Exception:
        print("  [telegram] unhandled agent error", flush=True)
        reply = "Maaf, terjadi error internal. Coba lagi sebentar."
    finally:
        done.set()
        heartbeat.join(timeout=0.2)
        if ok:
            # Kunci bubble progres sebagai catatan alur (tidak dihapus), lalu
            # kirim jawaban final sebagai pesan baru terpisah.
            live.finalize()
        else:
            live.clear()  # error/batal: buang bubble agar tidak menyisakan sampah
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
                            message_id=int(message.get("message_id") or 0),
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
