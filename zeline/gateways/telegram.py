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
from zeline import skill_publish
from zeline import interaction
from zeline.agent import CANCELLED_REPLY as _CANCELLED_SENTINEL
from zeline.agent import PROVIDER_STATUS_PREFIX
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
# Setelah berapa detik bubble status muncul. DUA ambang, karena "sedang kerja"
# dan "sedang menunggu model" adalah keadaan yang berbeda:
#   _STATUS_AFTER_SECONDS  → header 🧑🏻‍💻 Working, HANYA setelah ada tool jalan.
#   _THINKING_AFTER_SECONDS→ header 💭 Thinking, untuk turn tanpa tool sama sekali
#                            (sapaan/tanya ringan) yang kebetulan lambat.
# Dulu hanya ada satu ambang berbasis waktu, jadi "hallo" yang kena provider
# lambat ikut dilabeli "Working" padahal nol pekerjaan dilakukan.
_STATUS_AFTER_SECONDS = 30.0
_THINKING_AFTER_SECONDS = 60.0
REPOSITORY_HEADER = "## Repository Archive\n\n| # | Repository | Link |\n|---|------------|------|\n"
_URL_RE = re.compile(r"https?://[^\s<>\])}]+")

# Cache katalog /models per base_url (TTL) agar picker /model tidak memanggil
# network berkali-kali (tap provider lalu render model, lalu baca capabilities).
# _MODELS_CACHE menyimpan daftar id; _MODEL_META_CACHE menyimpan entri mentah
# (capabilities dst) dari payload YANG SAMA, supaya konfirmasi ganti model tidak
# perlu memukul /models untuk kedua kalinya (dulu inilah yang bikin tap model
# terasa "nggak ngapa-ngapain" lalu harus ditap 2x).
_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_MODEL_META_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_MODELS_CACHE_TTL = 300.0  # detik

# Retry untuk error jaringan sementara. Method yang MENGIRIM hasil ke user
# diretry supaya reply tidak hilang saat koneksi Termux drop; getUpdates &
# answerCallbackQuery TIDAK diretry (punya jalur/looping sendiri, dan callback
# cepat basi). editMessageText dibiarkan sekali (progress bubble, non-kritis).
# sendChatAction sengaja TIDAK diretry: dipanggil sangat sering dari heartbeat
# dan non-kritis; retry+sleep malah bikin heartbeat tersendat.
_API_RETRIES = 3
_RETRYABLE_METHODS = frozenset({"sendMessage", "sendDocument"})
# Baris progres (bubble '⏰ Processing', edit feed tool) BUKAN hal kritis. Di
# jaringan Termux yang sering drop, memanggilnya dengan timeout 65s + retry
# akan MENAHAN loop agent tiap update → efek 'macet/lambat/cek-cek doang' dan
# bubble ilang-ilangan. Jadi semua panggilan UI-progres pakai timeout pendek &
# attempts=1: kalau gagal, dilewati diam-diam, agent jalan terus. Hanya jawaban
# akhir (sendMessage biasa) yang tetap diretry supaya tidak pernah hilang.
_PROGRESS_TIMEOUT = 6
_PROGRESS_ATTEMPTS = 1

# Edit yang MERUPAKAN jawaban atas tap tombol (picker provider/model) HARUS
# diretry. Dulu editMessageText selalu attempts=1, jadi satu ConnectionError
# sesaat (biasa di Termux) bikin tap PERTAMA seolah tidak ngapa-ngapain dan aes
# harus tap 2x. Bedakan dari edit progres: yang ini interaktif & kritis.
_INTERACTIVE_ATTEMPTS = 3

# Verifikasi token saat startup: JANGAN menyerah setelah satu timeout. Latensi
# ke api.telegram.org dari Termux terukur 1.5-15.8s dan sesekali ReadTimeout;
# satu kegagalan bukan bukti token salah. Timeout longgar + beberapa percobaan.
_STARTUP_VERIFY_ATTEMPTS = 4
_STARTUP_VERIFY_TIMEOUT = 30

# Satu Session dipakai bersama supaya koneksi TLS ke api.telegram.org di-reuse
# (keep-alive). Handshake ulang tiap panggilan bikin setiap tap tombol bayar
# ~1s ekstra; dengan keep-alive turun ke ~0.3s. urllib3 pool-nya thread-safe.
_HTTP = requests.Session()

# Alias tampilan untuk prefix rute pada ID model. Router OpenAI-compatible
# memberi ID seperti `Gr/claude-opus-5` dan katalog `/models` tidak membawa nama
# panjang rutenya, jadi tanpa tabel ini halaman picker cuma menampilkan
# singkatan. Prefix di luar tabel dipakai apa adanya — menambah rute baru tidak
# mewajibkan mengedit ini.
_VENDOR_LABELS = {
    "gr": "GoRouter",
    "tabi": "TabiToken",
    "cx": "Codex",
    "nvidia": "NVIDIA",
    "oc": "OpenModel",
    "bai": "B.ai",
}


def _telegram_commands() -> list[dict[str, str]]:
    """Menu command ringkas seperti surface Telegram Zeline."""
    return [
        {"command": "start", "description": "Start Zeline"},
        {"command": "model", "description": "Switch model"},
        {"command": "status", "description": "View runtime status"},
        {"command": "repository", "description": "Download repository archive"},
        {"command": "deleterepository", "description": "Delete a repository entry"},
        {"command": "stop", "description": "Stop the active turn"},
        {"command": "new", "description": "Start a new session"},
        {"command": "version", "description": "Show version and check for updates"},
        {"command": "update", "description": "Update Zeline to the latest release"},
    ]


def _tool_names_for_profile(profile: str) -> list[str]:
    from zeline.tools import TOOL_DEFS
    return [definition.name for definition in TOOL_DEFS if profile in definition.profiles]


def _safe_progress_line(line: str, limit: int = 200) -> str:
    """Ringkas satu baris feed ke ``limit`` char TANPA merusak tag HTML.

    Bug yang diperbaiki: baris progress bisa mengandung `<pre>…</pre>` / `<code>…`.
    Truncation mentah (`[:200]`) dapat memotong di tengah tag penutup sehingga
    Telegram menolak `editMessageText` ("Can't find end tag ... pre") dan seluruh
    bubble progres macet. Di sini kita truncate ISI di dalam tag lebih dulu,
    lalu jamin setiap `<pre>`/`<code>` yang dibuka punya penutupnya.
    """
    # Ratakan newline agar satu baris (Telegram feed satu baris per aktivitas).
    text = line.replace("\n", " ").strip()
    if len(text) <= limit:
        candidate = text
    else:
        candidate = text[:limit].rstrip()
    # Rebalance tag pre/code yang mungkin terpotong / memang multiline.
    for tag in ("pre", "code"):
        opens = candidate.count(f"<{tag}>")
        closes = candidate.count(f"</{tag}>")
        if opens > closes:
            # Buang penutup parsial di ujung (mis. "</pr") lalu tutup rapi.
            candidate = re.sub(rf"<\s*/\s*{tag}?[^>]*$", "", candidate).rstrip()
            candidate += f"</{tag}>" * (opens - closes)
    return candidate


def _terminal_progress(command: str, *, search: bool = False) -> str:
    """Preview terminal satu baris ringkas dengan akhiran '...'.

    Menghindari blok <pre> multi-baris yang memicu kartu COPY CODE besar di Telegram.
    Cukup satu baris inline <code>command...</code> yang rapi dan padat.
    """
    cmd = command.replace("\n", " ").strip()
    limit = 70
    if len(cmd) > limit:
        cmd = cmd[:limit].rstrip() + "…"
    escaped = html.escape(cmd, quote=False)
    return f"💻 <code>{escaped}</code>"


def _is_search_command(command: str) -> bool:
    """True bila perintah shell bertujuan pencarian/riset informasi."""
    low = command.lower()
    return any(k in low for k in ("search", "researching", "curl ", "jina.ai", "duckduckgo", "google.com/search"))


def _short_path(path: str) -> str:
    """Tampilkan hanya nama file (basename), bukan path lokal panjang.

    User minta feed baca-file ringkas: '📖 Reading agent.py', bukan
    '📖 Reading /data/data/com.termux/files/home/....'. Selalu ambil segmen
    terakhir dari path.
    """
    cleaned = str(path).strip().rstrip("/")
    if not cleaned:
        return ""
    return cleaned.rsplit("/", 1)[-1]


def _tool_progress_text(name: str, arguments: dict[str, Any]) -> str:
    """Render one distinct HTML-safe progress message per real tool call.

    Ikon & label (English) mengikuti preferensi operator. Web search & deep
    research di-collapse jadi satu baris; aksi file (read/write/edit) selalu
    tampil per pemanggilan supaya user lihat SEMUA yang dikerjakan.
    """
    if name == "load_skill":
        return f"📚 Reading skill: {html.escape(str(arguments.get('name', ''))[:100])}"
    if name == "run_shell":
        command = str(arguments.get("command", ""))
        line = _terminal_progress(command, search=_is_search_command(command))
        if arguments.get("background"):
            return f"🚀 Starting background process {line}" if line else "🚀 Starting background process…"
        return line
    if name == "process_control":
        action = str(arguments.get("action", "")).strip().lower()
        job = html.escape(str(arguments.get("job_id", ""))[:40], quote=False)
        labels = {
            "list": "📋 Listing background processes…",
            "poll": f"⏱ Checking background process <code>{job}</code>…" if job else "⏱ Checking background process…",
            "log": f"📜 Reading process log <code>{job}</code>" if job else "📜 Reading process log…",
            "kill": f"🛑 Stopping background process <code>{job}</code>" if job else "🛑 Stopping background process…",
        }
        return labels.get(action, "⚙️ Managing background process…")
    if name == "execute_code":
        code = str(arguments.get("code", "")).strip()
        first = html.escape((code.splitlines() or ["code"])[0][:100], quote=False)
        return f"🐍 Running code: <code>{first}</code>…"
    if name == "manage_skill":
        skill_name = html.escape(str(arguments.get("name", ""))[:100], quote=False)
        action = str(arguments.get("action", "")).strip().lower()
        file_path = html.escape(str(arguments.get("file_path", ""))[:120], quote=False)
        if action in {"list", "inventory"}:
            return "🗂 Reviewing saved skills…"
        if action == "create":
            return f"💡 Saving skill: <code>{skill_name}</code>"
        if action == "write_file":
            return f"📄 Writing <code>{file_path or 'file'}</code> in skill <code>{skill_name}</code>"
        if action == "delete":
            merged = html.escape(str(arguments.get("absorbed_into", ""))[:100], quote=False)
            if merged:
                return f"🧹 Merging skill <code>{skill_name}</code> into <code>{merged}</code>"
            return f"🗑 Removing skill: <code>{skill_name}</code>"
        target = f" <code>{file_path}</code>" if file_path else ""
        return f"📝 Updating skill: <code>{skill_name}</code>{target}"
    if name in {"add_memory", "remove_memory"}:
        return "🧠 Saving to memory…"
    if name == "system_env":
        return "🧰 Checking system environment…"
    path = html.escape(_short_path(str(arguments.get("path", "")))[:120], quote=False)
    if name == "read_file":
        # Zeline read_file membaca seluruh file (tanpa offset/limit); rentang
        # baris hanya ditampilkan bila argumen offset/limit memang dikirim.
        if "offset" in arguments or "limit" in arguments:
            offset = max(1, int(arguments.get("offset", 1) or 1))
            limit = max(1, int(arguments.get("limit", 500) or 500))
            return f"📖 Reading file <code>{path}</code> L{offset}-{offset + limit - 1}"
        return f"📖 Reading file <code>{path}</code>"
    if name == "write_file":
        return f"📝 Writing file <code>{path}</code>"
    if name == "edit_file":
        return f"🎬 Editing file <code>{path}</code>"
    if name == "patch_file":
        return f"🎬 Editing file <code>{path}</code>"
    if name == "search_files":
        query = html.escape(str(arguments.get("query", ""))[:200], quote=False)
        return f"🔎 Searching files: {query}" if query else "🔎 Searching files…"
    if name == "update_task":
        status = html.escape(str(arguments.get("status", "pending"))[:40], quote=False)
        task = html.escape(str(arguments.get("task", ""))[:120], quote=False)
        return f"📋 Updating tasks: {status} · {task}" if task else f"📋 Updating tasks: {status}"
    if name == "web_search":
        # Searching (web): satu baris ringkas, di-collapse. Subjek utama saja.
        query = str(arguments.get("query", "")).strip()
        subject = html.escape((query.split() or [""])[0][:40], quote=False)
        return f"🌐 Searching web: {subject}…" if subject else "🌐 Searching web…"
    if name == "web_fetch":
        # Baca sumber web tidak ditampilkan sebagai baris terpisah (biar bersih).
        return ""
    if name == "deep_research":
        query = html.escape(str(arguments.get('query', ''))[:100], quote=False)
        return f"🌐 Researching: {query}…" if query else "🌐 Researching…"
    if name == "generate_image":
        prompt = html.escape(str(arguments.get("prompt", ""))[:100], quote=False)
        return f"🎨 Generating image: {prompt}…" if prompt else "🎨 Generating image…"
    if name == "analyze_media":
        return "🖼 Looking at image…"
    if name == "http_request":
        url = html.escape(str(arguments.get("url", ""))[:120], quote=False)
        return f"🔗 Calling API: {url}" if url else "🔗 Calling API…"
    if name == "delegate_task":
        goal = html.escape(str(arguments.get("goal", ""))[:100], quote=False)
        return f"🤝 Delegating: {goal}…" if goal else "🤝 Delegating subtask…"
    # Fallback: satu baris, argumen pertama saja, TANPA newline.
    first_val = ""
    if isinstance(arguments, dict) and arguments:
        first_val = html.escape(str(next(iter(arguments.values())))[:80], quote=False)
    label = html.escape(name.replace("_", " "), quote=False)
    return f"🔧 {label}: {first_val}" if first_val else f"🔧 {label}"


def _progress_category(line: str) -> str | None:
    """Kategori baris feed untuk collapse. None = baris unik (jangan digabung).

    HANYA web search & deep research yang di-collapse jadi SATU baris (repetitif,
    sering banyak query mirip). Aksi file (📖 Reading / 📝 Writing / 🎬 Editing),
    shell, run code TIDAK di-collapse — tiap file & command tampil sendiri supaya
    user lihat SEMUA yang dikerjakan, bukan cuma satu ringkasan.
    """
    if line.startswith("🌐 Researching"):
        return "research"
    if line.startswith("🌐"):
        return "search"
    return None


# Urutan tampilan tetap agar feed rapi & logis, apa pun urutan model memanggil
# tool: searching → researching, sisanya tampil kronologis apa adanya.
_CATEGORY_ORDER = {"search": 0, "research": 1}


def _ordered_lines(lines: list[str]) -> list[str]:
    """Urutkan baris feed berdasarkan kategori tetap, stabil untuk kategori lain."""
    def key(item: tuple[int, str]) -> tuple[int, int]:
        index, line = item
        category = _progress_category(line)
        rank = _CATEGORY_ORDER.get(category, 99) if category else 99
        return (rank, index)
    return [line for _index, line in sorted(enumerate(lines), key=key)]


def _finalize_line(line: str) -> str:
    """Ubah baris fase-kerja jadi bentuk 'selesai'.

    Web search & deep research (yang di-collapse jadi satu baris) diselesaikan
    jadi '📖 Read web · <subjek>' — TETAP menyebut subjek yang dicari, bukan
    penanda generik 'data/other'. Baris file (📖 Reading <file>) dibiarkan apa
    adanya supaya SEMUA file yang dibaca tetap terlihat.
    """
    prefixes = ("🌐 Searching", "🌐 Researching")
    for prefix in prefixes:
        if line.startswith(prefix):
            # Ambil subjek setelah ikon+verb, buang elipsis/trailing.
            subject = line[len(prefix):].strip().strip("…").strip()
            return f"📖 Read web · {subject}" if subject else "📖 Read web"
    return line


def _tool_result_text(name: str, arguments: dict[str, Any], result: str) -> str | None:
    """Render hanya hasil nyata yang bernilai sebagai progress terpisah."""
    if name == "manage_skill" and not result.startswith("ERROR"):
        # ``list`` cuma orientasi internal (cek duplikat sebelum menyimpan);
        # mengirim inventaris skill ke chat sebagai "Improvement" itu bising dan
        # membocorkan nama skill private tanpa alasan.
        if str(arguments.get("action", "")).strip().lower() in {"list", "inventory"}:
            return None
        return f"📒 Improvement: {html.escape(result[:1000], quote=False)}"
    return None


#: Ikon per kelas status provider. Diambil dari ARTI status menurut router
#: (lihat ``agent.PROVIDER_STATUS_HINTS``), bukan dari rentang angka:
#:   🪫 = batas habis (kunci/izin/kuota/saldo/rate)  → 401, 402, 403, 429
#:   ⏱️ = provider sedang tidak tersedia             → 503
#:   ⏰ = gateway bermasalah / timeout                → 502, 504
#:   ⚠️ = sisanya (bad request, model salah, 500)
_STATUS_BADGE_ICONS: dict[int, str] = {
    401: "🪫", 402: "🪫", 403: "🪫", 429: "🪫",
    503: "⏱️",
    502: "⏰", 504: "⏰",
}


def _format_agent_error(message: str) -> str:
    """Beri ikon yang tepat untuk pesan error agent (English).

    Untuk error berstatus HTTP, ikon DAN teksnya berasal dari satu sumber
    (``agent.PROVIDER_STATUS_HINTS``) supaya badge tidak pernah bertentangan
    dengan isi pesan. Dulu 403 memakai teks "API key invalid — jalankan zeline
    setup"; padahal di router 403 = ``permission_error/insufficient_quota``
    (kuota habis, ada cooldown), dan kunci yang benar-benar salah memberi 401.
    Awalan "The provider returned HTTP 403 — " dipotong karena kodenya sudah
    tampil di badge; tanpa ini user membaca "403" dua kali dalam satu baris.
    """
    low = message.lower()
    # Status HTTP diperiksa DULU. Pesan 504 mengandung kata "timed out", jadi
    # kalau cabang timeout jalan lebih dulu, gateway-timeout dari provider
    # salah dilabeli "Read Timeout" milik klien — dua penyebab berbeda dengan
    # tindakan berbeda. Read timeout Zeline sendiri tidak pernah membawa kode
    # HTTP, jadi urutan ini aman.
    status_match = re.search(r"http\s*(\d{3})", low)
    if status_match:
        status = int(status_match.group(1))
        icon = _STATUS_BADGE_ICONS.get(status, "⚠️")
        detail = message
        prefix = f"{PROVIDER_STATUS_PREFIX}{status} — "
        if detail.startswith(prefix):
            detail = detail[len(prefix):]
        return f"{icon} {status} — {detail}"
    if "timed out" in low or "did not respond" in low or ("stream" in low and "interrupted" in low):
        # Tenangkan user: timeout TIDAK menjatuhkan percakapan —
        # riwayat tetap utuh, tinggal kirim lagi atau /new untuk sesi baru.
        return (
            f"⚠️ Read Timeout — {message}\n\n"
            "No messages were dropped — the conversation continues unchanged. "
            "Send again, or use /new to start a fresh session."
        )
    if "unauthorized" in low or "rate limited" in low or "out of credits" in low or "quota" in low:
        # Tanpa kode HTTP di pesan (mis. "rate limited"), pakai label generik.
        return f"🪫 Quota/Auth — {message}"
    if "too long" in low or "maksimum" in low or ("context" in low and "limit" in low) or "terlalu panjang" in low:
        return f"🪫 Limit Text/Context — {message}"
    return f"⚠️ Zeline hit a problem — {message}"


#: Emoji header status kerja. Diminta user: 🧑🏻‍💻, bukan ⏳ — jam pasir dipakai
#: untuk menunggu, dan menunggu bukan hal yang sama dengan mengerjakan.
WORKING_ICON = "🧑🏻‍💻"

#: Header untuk turn yang cuma menunggu model (belum satu pun tool jalan).
THINKING_ICON = "💭"

#: Berapa lama satu fase "menunggu provider" harus berjalan sebelum status
#: menyebutkannya. Di bawah ini, menunggu itu normal dan tidak perlu diumumkan;
#: di atasnya, user berhak tahu bahwa yang lambat adalah provider — bukan Zeline
#: yang menggantung.
_PROVIDER_WAIT_NOTE_SECONDS = 20.0

#: Ekor yang ditambahkan ke baris status saat provider yang bikin lambat.
PROVIDER_WAIT_NOTE = ", waiting for provider response - streaming"


def _working_status_text(
    elapsed_seconds: float,
    *,
    iteration: int | None = None,
    maximum: int | None = None,
    remaining_seconds: float | None = None,
    working: bool = True,
    provider_wait_seconds: float | None = None,
) -> str:
    """Header status live: bukti agent MASIH kerja, bukan menggantung diam.

    Format satu baris: waktu jalan + bahwa /stop tersedia.

    ``working=False`` dipakai saat NOL tool sudah jalan: labelnya "Thinking",
    bukan "Working". Menyebut sapaan yang lambat sebagai "Working" itu salah —
    tidak ada pekerjaan yang dilakukan, yang lama adalah provider.

    ``provider_wait_seconds`` = lama fase menunggu provider yang sedang berjalan.
    Kalau melewati ``_PROVIDER_WAIT_NOTE_SECONDS``, baris statusnya menyebutkan
    itu. Tanpa ini, turn yang macet menunggu upstream terlihat identik dengan
    turn yang sedang sibuk bekerja, dan user tidak bisa membedakan "Zeline
    lambat" dari "provider lambat".
    """
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)
    clock = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
    # Keep the user-facing heartbeat deliberately minimal. Iteration/remaining
    # are still tracked internally for turn control, but exposing them makes
    # every ordinary chat look like a noisy job runner.
    icon, label = (WORKING_ICON, "Working") if working else (THINKING_ICON, "Thinking")
    line = f"{icon} {label} — {clock} · /stop to cancel"
    if provider_wait_seconds is not None and provider_wait_seconds >= _PROVIDER_WAIT_NOTE_SECONDS:
        line += PROVIDER_WAIT_NOTE
    return line


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
      - ``waiting``: menunggu respons LLM.
      - ``tool``: sedang menjalankan tool → feed aktivitas.

    Header ``⏳ Working — …`` hanya muncul setelah turn berjalan lebih lama dari
    ``_STATUS_AFTER_SECONDS``. Ini yang bikin user tidak pernah "didiemin":
    pertanyaan ringan tetap bersih tanpa bubble, tapi kerja panjang selalu
    menunjukkan jam berjalan, langkah ke berapa, sisa budget, dan bahwa /stop
    bisa dipakai. Aman dipakai dari worker heartbeat dan callback tool.
    """

    def __init__(self, api: str, chat_id: int, *, max_lines: int = 14, model: str = ""):
        self.api = api
        self.chat_id = chat_id
        self.max_lines = max_lines
        self.model = model
        self.message_id: int | None = None
        self.lines: list[str] = []
        self.phase = "waiting"
        self.phase_started = time.monotonic()
        # Awal turn (bukan awal fase) → dipakai untuk jam "Working — Nm Ns".
        self.turn_started = time.monotonic()
        self.iteration: int | None = None
        self.maximum: int | None = None
        self._last_text: str | None = None
        self._lock = threading.Lock()

    def _header(self) -> str:
        """Baris status hidup; kosong selama turn masih pendek.

        Ambangnya tergantung apakah ada pekerjaan NYATA. Tanpa satu pun tool
        jalan (sapaan/tanya ringan), yang terjadi hanyalah menunggu provider:
        itu diberi ambang lebih longgar dan label "Thinking". Begitu tool
        pertama jalan, statusnya jadi "Working" dengan ambang normal.
        """
        elapsed = time.monotonic() - self.turn_started
        working = bool(self.lines)
        threshold = _STATUS_AFTER_SECONDS if working else _THINKING_AFTER_SECONDS
        if elapsed < threshold:
            return ""
        budget = float(getattr(config, "MAX_TURN_SECONDS", 0) or 0)
        remaining = (budget - elapsed) if budget else None
        # Hanya fase ``waiting`` yang berarti "menunggu provider". Saat tool
        # sedang jalan, lambatnya bukan urusan provider, jadi jangan disebut.
        provider_wait = (
            time.monotonic() - self.phase_started if self.phase == "waiting" else None
        )
        return _working_status_text(
            elapsed,
            iteration=self.iteration,
            maximum=self.maximum,
            remaining_seconds=remaining,
            working=working,
            provider_wait_seconds=provider_wait,
        )

    def set_iteration(self, current: int | None, maximum: int | None) -> None:
        with self._lock:
            self.iteration = current
            self.maximum = maximum

    def _render(self) -> str:
        # Feed aktivitas tool DULU, baris status di PALING BAWAH, dipisah satu
        # baris kosong. Diminta user: status yang mengambang di atas feed bikin
        # baris terbaru (yang justru paling penting) terdorong ke bawah, dan
        # jam yang berubah tiap detik menarik mata ke tempat yang salah.
        ordered = _ordered_lines(self.lines)[-self.max_lines:]
        header = self._header()
        if header and ordered:
            return "\n".join(ordered) + "\n\n" + header
        if header:
            return header
        return "\n".join(ordered)

    def _push_locked(self, force: bool = False, allow_create: bool = True) -> None:
        # Bubble progres dibuat saat ada aktivitas tool nyata, ATAU saat turn
        # sudah berjalan lama tanpa kabar (header status) — supaya user tidak
        # merasa didiamkan. Pertanyaan ringan yang selesai cepat tetap bersih.
        if self.message_id is None and not allow_create and not self._header():
            return
        text = self._render()
        # Tanpa header lagi: kalau belum ada baris aktivitas, teksnya kosong →
        # jangan pernah kirim/edit bubble kosong (Telegram tolak teks kosong &
        # bikin bubble hampa). Bubble baru hanya lahir saat ada aktivitas nyata.
        if not text.strip():
            return
        if text == self._last_text and not force:
            return
        self._last_text = text
        if self.message_id is None:
            payload = _api_call(
                self.api, "sendMessage", chat_id=self.chat_id,
                text=text, parse_mode="HTML",
                timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
            )
            if payload and isinstance(payload.get("result"), dict):
                self.message_id = payload["result"].get("message_id")
        else:
            _api_call(
                self.api, "editMessageText", chat_id=self.chat_id,
                message_id=self.message_id, text=text, parse_mode="HTML",
                timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
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

        Baris fase-kerja jadi bentuk selesai (Searching→Reading), TANPA header
        '✅ Successful' (dihapus atas permintaan user). Kalau tidak ada aktivitas
        tool sama sekali (jawaban langsung), bubble dihapus agar tidak menyisakan
        pesan kosong.
        """
        with self._lock:
            if self.message_id is None:
                return
            if not self.lines:
                _api_call(
                    self.api, "deleteMessage",
                    chat_id=self.chat_id, message_id=self.message_id,
                    timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
                )
                self.message_id = None
                return
            body = "\n".join(_finalize_line(line) for line in _ordered_lines(self.lines)[-self.max_lines:])
            _api_call(
                self.api, "editMessageText", chat_id=self.chat_id,
                message_id=self.message_id, text=body, parse_mode="HTML",
                timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
            )

    def clear(self) -> None:
        """Hapus pesan status. Dipakai bila turn dibatalkan/error tanpa hasil."""
        with self._lock:
            if self.message_id is not None:
                _api_call(
                    self.api, "deleteMessage",
                    chat_id=self.chat_id, message_id=self.message_id,
                    timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
                )
                self.message_id = None

    def detach(self) -> None:
        """Kunci bubble progres saat ini & lepaskan supaya aktivitas berikutnya

        membuat bubble BARU di bawahnya. Dipakai sebelum mengirim bubble narasi
        model: aktivitas tool yang sudah terjadi dikunci jadi catatan (baris
        aktivitas apa adanya, tanpa header), lalu feed di-reset kosong. Efeknya
        urutan chat jadi rapi: [aktivitas tool] → [bubble penjelasan model] → …
        Bubble kosong (belum ada tool) cukup dilepaskan tanpa menyisakan sampah.
        """
        with self._lock:
            if self.message_id is None:
                self.lines = []
                self._last_text = None
                return
            if self.lines:
                body = "\n".join(_finalize_line(line) for line in _ordered_lines(self.lines)[-self.max_lines:])
                _api_call(
                    self.api, "editMessageText", chat_id=self.chat_id,
                    message_id=self.message_id, text=body, parse_mode="HTML",
                    timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
                )
            else:
                _api_call(
                    self.api, "deleteMessage",
                    chat_id=self.chat_id, message_id=self.message_id,
                    timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS,
                )
            self.message_id = None
            self.lines = []
            self._last_text = None
            self.phase = "waiting"
            self.phase_started = time.monotonic()


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
                _api_call(api, "sendChatAction", chat_id=chat_id, action="typing",
                          timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS)
            except Exception:
                pass
            live.tick()

    worker = threading.Thread(target=heartbeat, name=f"zeline-heartbeat-{chat_id}", daemon=True)
    worker.start()
    return worker


def _vendor_label(key: str) -> str:
    """Nama tampilan untuk satu grup model (segmen prefix sebelum '/').

    Router seperti 9Router memberi ID model berprefix rute (`Gr/…`, `tabi/…`,
    `cx/…`) dan `/models` hanya mengembalikan prefix itu di `owned_by` — tidak
    ada nama panjangnya. Tabel alias kecil ini membuat halaman picker terbaca
    ("GoRouter") alih-alih memaksa aes menghafal singkatan. Prefix yang tidak
    dikenal ditampilkan apa adanya, jadi tabel ini tidak wajib dirawat.
    """
    if not key:
        return "Other"
    return _VENDOR_LABELS.get(key.lower(), key)


def _model_vendor_groups(models: list[str]) -> list[tuple[str, list[int]]]:
    """Kelompokkan model per prefix rute, urut sesuai kemunculan di katalog.

    Mengembalikan indeks GLOBAL (posisi di `models`) supaya callback pemilihan
    model tetap `model:<provider>:<index>` dan jalur ganti model tidak berubah.
    """
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    for index, model in enumerate(models):
        key = model.split("/", 1)[0] if "/" in model else ""
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(index)
    return [(key, groups[key]) for key in order]


def _model_picker_payload(
    models: list[str],
    current_model: str,
    provider_index: int | None = None,
    provider_name: str = "",
    group_index: int = 0,
) -> tuple[str, dict[str, Any]]:
    """Bangun inline picker dengan callback pendek agar aman di batas 64 byte.

    Bila katalog provider berisi beberapa rute (mis. 9Router menyajikan Gr,
    tabi, dan cx sekaligus), model dipecah PER RUTE dan ditampilkan satu
    halaman per rute dengan tombol Next/Prev. Satu daftar 22 model campur
    membuat aes harus scroll jauh dan label harus memakai ID penuh supaya tidak
    ambigu; dipisah per rute, labelnya cukup nama modelnya saja dan nama rute
    naik ke teks status di atas tombol.
    """
    groups = _model_vendor_groups(models) if provider_index is not None else []
    if len(groups) > 1:
        return _grouped_model_picker_payload(
            models, current_model, provider_index, provider_name, groups, group_index
        )
    buttons = []
    # Deteksi label yang bakal tabrakan bila hanya diambil segmen terakhir.
    # Di router seperti 9Router, ID model berprefix rute (mis. `Gr/claude-opus-4-8`
    # dan `tabi/claude-opus-4-8`) → rsplit('/') membuat dua tombol identik tanpa
    # pembeda. Kalau segmen terakhir tidak unik, tampilkan ID PENUH agar jelas
    # provider/rute mana yang dipilih.
    tails = [model.rsplit("/", 1)[-1] for model in models]
    ambiguous = {tail for tail in tails if tails.count(tail) > 1}
    for index, model in enumerate(models):
        tail = model.rsplit("/", 1)[-1]
        # Tampilkan ID penuh bila segmen terakhir tidak unik (biar prefix rute
        # terlihat); selain itu segmen terakhir sudah cukup ringkas.
        label = model if tail in ambiguous else tail
        if model == current_model:
            label = f"✓ {label}"
        callback = f"model:{index}" if provider_index is None else f"model:{provider_index}:{index}"
        buttons.append({"text": label[:60], "callback_data": callback})
    # Model dengan label panjang (ID penuh) lebih enak dibaca satu per baris.
    per_row = 1 if ambiguous else 2
    rows = [buttons[index:index + per_row] for index in range(0, len(buttons), per_row)]
    if provider_index is not None:
        rows.append([{"text": "« Back", "callback_data": "provider:back"}])
    rows.append([{"text": "✗ Cancel", "callback_data": "model:cancel"}])
    return (
        (f"Select a model\n{provider_name} • {len(models)} models\nCurrent: {current_model or 'unknown'}" if provider_name else f"Select a model\nCurrent: {current_model or 'unknown'}"),
        {"inline_keyboard": rows},
    )


def _grouped_model_picker_payload(
    models: list[str],
    current_model: str,
    provider_index: int,
    provider_name: str,
    groups: list[tuple[str, list[int]]],
    group_index: int,
) -> tuple[str, dict[str, Any]]:
    """Satu halaman picker = satu rute. Next/Prev berputar antar rute."""
    total = len(groups)
    page = group_index % total  # berputar: Next di halaman terakhir kembali ke awal
    key, indices = groups[page]
    label_name = _vendor_label(key)

    buttons = []
    for index in indices:
        model = models[index]
        # Dalam satu rute prefix-nya sama, jadi nama model saja sudah jelas.
        label = model.rsplit("/", 1)[-1]
        if model == current_model:
            label = f"✓ {label}"
        buttons.append({"text": label[:60], "callback_data": f"model:{provider_index}:{index}"})
    per_row = 2 if all(len(button["text"]) <= 22 for button in buttons) else 1
    rows = [buttons[index:index + per_row] for index in range(0, len(buttons), per_row)]

    navigation = []
    if total > 2:
        navigation.append({"text": "‹ Prev", "callback_data": f"grp:{provider_index}:{(page - 1) % total}"})
    navigation.append({"text": "Next ›", "callback_data": f"grp:{provider_index}:{(page + 1) % total}"})
    rows.append(navigation)
    rows.append([{"text": "« Back", "callback_data": "provider:back"}])
    rows.append([{"text": "✗ Cancel", "callback_data": "model:cancel"}])

    header = f"{provider_name} › {label_name}" if provider_name else label_name
    next_label = _vendor_label(groups[(page + 1) % total][0])
    text = (
        f"Select a model ({page + 1}/{total})\n"
        f"{header} • {len(indices)} models\n"
        f"Current: {current_model or 'unknown'}"
    )
    return text, {"inline_keyboard": rows}


def _configured_providers() -> list[dict[str, str]]:
    cfg = config.stored_config_copy()
    stored = cfg.get("providers", {})
    providers: list[dict[str, str]] = []
    if isinstance(stored, dict):
        for slug, item in stored.items():
            if isinstance(item, dict) and item.get("base_url") and item.get("api_key"):
                providers.append({"slug": str(slug), **{key: str(value) for key, value in item.items()}})
    active = cfg.get("provider", {})
    # Provider identity is endpoint + credential, not a cosmetic display name.
    # Older configs can hold `9router` in the saved entry and `9Router` in the
    # active copy; comparing names inserts a duplicate `active` picker button.
    active_slug = next(
        (
            item["slug"]
            for item in providers
            if item.get("base_url", "").rstrip("/") == str(active.get("base_url", "")).rstrip("/")
            and item.get("api_key") == str(active.get("api_key", ""))
        ),
        "",
    )
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


def _fetch_models_catalog(base_url: str, api_key: str, *, timeout: int = 12) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Ambil /models SEKALI lalu isi kedua cache (id list + metadata per id).

    Mendukung berbagai format (OpenAI, Anthropic, Ollama, vLLM) dan endpoint
    alternatif (/models, /v1/models, /api/tags).
    """
    base = str(base_url or "").rstrip("/")
    if not base:
        return [], {}
    now = time.monotonic()
    cached_ids = _MODELS_CACHE.get(base)
    cached_meta = _MODEL_META_CACHE.get(base)
    if cached_ids and cached_meta and now - cached_ids[0] < _MODELS_CACHE_TTL:
        return cached_ids[1], cached_meta[1]

    endpoints = [f"{base}/models"]
    if not base.endswith("/v1"):
        endpoints.append(f"{base}/v1/models")
    else:
        root_base = base[:-3]
        if root_base:
            endpoints.append(f"{root_base}/models")
    endpoints.append(f"{base}/api/tags")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    ids: list[str] = []
    meta: dict[str, dict[str, Any]] = {}

    for endpoint in dict.fromkeys(endpoints):
        try:
            response = _HTTP.get(endpoint, headers=headers, timeout=timeout)
            if not response.ok:
                continue
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue

        items = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in ("data", "models", "data_list"):
                val = payload.get(key)
                if isinstance(val, list):
                    items = val
                    break

        for item in items:
            if isinstance(item, str) and item.strip():
                m_id = item.strip()
                ids.append(m_id)
                meta[m_id] = {"id": m_id}
            elif isinstance(item, dict):
                m_id = str(item.get("id") or item.get("name") or item.get("model") or item.get("model_name") or "").strip()
                if m_id:
                    ids.append(m_id)
                    meta[m_id] = item

        ids = list(dict.fromkeys(ids))
        if ids:
            break

    if ids:
        _MODELS_CACHE[base] = (now, ids)
        _MODEL_META_CACHE[base] = (now, meta)
    return ids, meta


def _discover_provider_models(provider: dict[str, str]) -> list[str]:
    ids, _ = _fetch_models_catalog(provider.get("base_url", ""), provider.get("api_key", ""))
    return ids or ([provider.get("model", "")] if provider.get("model") else [])


def _discover_models() -> list[str]:
    """Ambil katalog model live dari provider OpenAI-compatible."""
    ids, _ = _fetch_models_catalog(config.BASE_URL, config.API_KEY, timeout=20)
    return ids or ([config.MODEL] if config.MODEL else [])


def _provider_label() -> str:
    """Safe provider name for status; never exposes credentials."""
    provider = config.stored_config_copy().get("provider", {})
    name = str(provider.get("name", "")).strip()
    return name or (urlparse(config.BASE_URL).hostname or "unknown")


def _fetch_model_capabilities(provider: dict[str, str] | None, model_id: str) -> dict[str, Any]:
    """Return the raw capabilities/metadata dict for one model id, or {}.

    Reads from the SHARED /models catalog cache (`_fetch_models_catalog`), which
    the picker already warmed a moment earlier — so confirming a model switch
    costs no extra network round-trip. Safe: returns {} on any error so the
    caller degrades gracefully.
    """
    base = (provider or {}).get("base_url") if provider else config.BASE_URL
    key = (provider or {}).get("api_key") if provider else config.API_KEY
    _, meta = _fetch_models_catalog(str(base or ""), str(key or ""))
    entry = meta.get(model_id)
    return entry if isinstance(entry, dict) else {}


def _format_token_count(value: Any) -> str | None:
    """Human-friendly token count: 1000000 -> '1M', 128000 -> '128K'."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number >= 1_000_000 and number % 1_000_000 == 0:
        return f"{number // 1_000_000}M"
    if number >= 1_000:
        trimmed = number / 1_000
        return f"{trimmed:.0f}K" if trimmed == int(trimmed) else f"{trimmed:.1f}K"
    return str(number)


def _model_switch_text(selected: str, provider: dict[str, str] | None) -> str:
    """Build the model-switch confirmation with capability details."""
    provider_name = provider.get("name", provider.get("slug", "")) if provider else _provider_label()

    # Cari label rute (mis. "GoRouter" untuk prefix "Gr") untuk tampilan yang
    # sama dengan picker — supaya user tahu rute mana yang dipilih.
    route_label = ""
    if "/" in selected:
        prefix = selected.split("/", 1)[0]
        route_label = f" › {_vendor_label(prefix)}"

    lines = [
        "╭───────────♻️",
        "├ <b>Model Switched</b>",
        f"├ Provider : <code>{html.escape(str(provider_name))}{html.escape(route_label)}</code>",
        f"├ Model : <code>{html.escape(selected)}</code>",
    ]

    meta = _fetch_model_capabilities(provider, selected)
    caps = meta.get("capabilities") if isinstance(meta, dict) else None
    caps = caps if isinstance(caps, dict) else {}

    context_window = _format_token_count(caps.get("contextWindow"))
    max_output = _format_token_count(caps.get("maxOutput"))
    if context_window:
        lines.append(f"├ Context : {context_window} tokens")
    if max_output:
        lines.append(f"├ Max out : {max_output} tokens")

    lines.append("╰ Saved to config.yaml (--global)")
    return "\n".join(lines)


def _new_session_text() -> str:
    return (
        "╭───────────────────🌟\n"
        "├ Session reset! Starting fresh\n"
        f"├ Model : {config.MODEL}\n"
        f"├ Provider : {_provider_label()}\n"
        "├ Context : 0 tokens\n"
        f"├ Endpoint : {config.BASE_URL}\n"
        "╰ Tip : Use /status to check this session."
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


def _version_reply() -> str:
    """Version card for /version. Read-only, and honest when offline."""
    from zeline import self_update

    report = self_update.version_report()
    lines = [
        "╭───────────────📦",
        "├ <b>Zeline Version</b>",
        f"├ Installed : <code>{html.escape(str(report['current']))}</code>",
    ]
    if report["error"]:
        # Say the check failed rather than implying the installed build is current.
        lines.append("├ Latest : <i>unknown (release check failed)</i>")
    else:
        lines.append(f"├ Latest : <code>{html.escape(str(report['latest']))}</code>")
        lines.append("├ Status : Up to date" if report["up_to_date"] else "├ Status : Update available — /update")
    if report["checkout"]:
        lines.append("├ Mode : source checkout")
    if report["updating"]:
        lines.append("├ Note : an update is running now")
    lines.append("╰ Platform : Telegram")
    return "\n".join(lines)


def _start_update_reply(chat_id: int, allowed: list[Any]) -> str:
    """Kick off a detached self-update and describe what will happen.

    Owner-gated deliberately. An update restarts the gateway and replaces the
    installed package for *every* user of this bot, so it is not something a
    guest in an open allowlist may trigger. When ``allowed`` is empty the bot is
    public, and there is no owner to authorise it at all.
    """
    from zeline import self_update

    if not allowed:
        return (
            "This bot has no owner allowlist, so /update is disabled. "
            "Set one with <code>zeline setup</code>, or update from a shell: <code>zeline update</code>"
        )
    if str(chat_id) != str(allowed[0]):
        return "Only the bot owner can run /update."

    report = self_update.version_report()
    if report["checkout"]:
        # In a checkout, `zeline update` reinstalls from local source. Doing that
        # silently from chat would install whatever is in the working tree,
        # committed or not -- surprising, and not what "update" means here.
        return (
            f"Running from a source checkout (<code>{html.escape(report['checkout'])}</code>). "
            "Update it there so you control exactly what gets installed:\n"
            "<code>cd " + html.escape(report["checkout"]) + " &amp;&amp; git pull &amp;&amp; zeline update</code>"
        )
    if not report["error"] and report["up_to_date"]:
        return f"Already on the latest release (<code>{html.escape(str(report['current']))}</code>). Nothing to do."

    started, message = self_update.start_background_update(f"telegram:{chat_id}")
    if not started:
        return html.escape(message)
    target = f" → <code>{html.escape(str(report['latest']))}</code>" if report["latest"] else ""
    return (
        f"Updating from <code>{html.escape(str(report['current']))}</code>{target}\n"
        "The gateway will finish in-flight work, stop, install, and restart. "
        "It will be unreachable for about a minute; progress lands here."
    )


#: Jendela di mana /stop berikutnya untuk identity yang sama dianggap dobel-tap
#: dan tidak dibalas lagi. Cukup panjang untuk menutupi jeda polling + retry
#: Telegram, cukup pendek supaya /stop iseng jam berikutnya tetap dijawab.
_STOP_ECHO_SECONDS = 90.0

_recent_stops: dict[str, float] = {}
_recent_stops_lock = threading.Lock()


def _note_stop(identity: str) -> None:
    """Catat bahwa /stop untuk identity ini BARU SAJA dikonfirmasi."""
    with _recent_stops_lock:
        now = time.monotonic()
        _recent_stops[identity] = now
        # Buang entri kedaluwarsa supaya dict tidak tumbuh di bot ramai.
        for key, when in list(_recent_stops.items()):
            if now - when > _STOP_ECHO_SECONDS:
                _recent_stops.pop(key, None)


def _stopped_recently(identity: str) -> bool:
    with _recent_stops_lock:
        when = _recent_stops.get(identity)
    return when is not None and (time.monotonic() - when) <= _STOP_ECHO_SECONDS


def _consume_stop(identity: str) -> bool:
    """True bila turn ini yang dibatalkan oleh /stop — dan tandai sudah dipakai.

    Dipakai worker turn untuk tahu bahwa pembatalan SUDAH dilaporkan oleh
    handler /stop, sehingga ia tidak mengirim "Stopped." sebagai pesan kedua.
    """
    with _recent_stops_lock:
        when = _recent_stops.get(identity)
        if when is None or (time.monotonic() - when) > _STOP_ECHO_SECONDS:
            return False
        return True


def _handle_command_update(
    api: str,
    text: str,
    sessions,
    identity: str,
    chat_id: int,
    *,
    stop_event,
    tool_profile: str,
    allowed: list[Any] | None = None,
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
                "Command to setup :\n"
                "/model — Switch model\n"
                "/status — View runtime status\n"
                "/version — Show version, check for updates\n"
                "/update — Update to the latest release\n"
                "/stop — Stop the active turn\n"
                "/new — Start a new session\n\n"
                "Send a message to start a task"
            ),
        )
        return True
    if command == "/version":
        _api_call(api, "sendMessage", chat_id=chat_id, text=_version_reply(), parse_mode="HTML")
        return True
    if command == "/update":
        _api_call(
            api, "sendMessage", chat_id=chat_id,
            text=_start_update_reply(chat_id, allowed or []), parse_mode="HTML",
        )
        return True
    if command == "/status":
        status = sessions.status(identity)
        _api_call(
            api, "sendMessage", chat_id=chat_id,
            text=(
                "╭───────────────🚥\n"
                "├ <b>Zeline Gateway Status</b>\n"
                f"├ Session ID : <code>{html.escape(str(status['session_id']))}</code>\n"
                f"├ Provider : <code>{html.escape(_provider_label())}</code>\n"
                f"├ Model : <code>{html.escape(str(status['model']))}</code>\n"
                f"├ Title : {html.escape(str(status['title']))}\n"
                f"├ Context : {html.escape(str(status['context']))}\n"
                f"├ Agent Running : {'Yes' if status['agent_running'] else 'No'}\n"
                "╰ Platform : Telegram"
            ), parse_mode="HTML",
        )
        return True
    if command == "/repository":
        repository = _ensure_repository()
        if not _send_document(api, chat_id, repository):
            _api_call(api, "sendMessage", chat_id=chat_id, text="Failed to send the repository archive.")
        return True
    if command == "/deleterepository":
        if not args:
            reply = "Usage: /deleterepository <project or link>"
        else:
            result = _delete_task(args)
            reply = {
                "deleted": "Repository entry deleted from repository.md.",
                "not_found": "Entry not found. Mention its project name or link.",
                "ambiguous": "Multiple entries matched. Use a more specific project name or link.",
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
        if stopped:
            _note_stop(identity)
            # SATU pesan saja. Turn yang dibatalkan TIDAK ikut mengirim
            # "Stopped." (lihat _send_agent_reply) dan refleksi dilewati, jadi
            # inilah satu-satunya balasan untuk /stop.
            reply = (
                f"❄️ Stopped — {title}\n"
                "The running step was force-killed. Session and history are intact."
            )
        elif _stopped_recently(identity):
            # /stop kedua (dobel-tap, atau update yang sama dikirim ulang
            # Telegram) tiba setelah turn benar-benar berhenti. Membalas
            # "No active task to stop." di sini yang bikin satu pembatalan
            # terlihat seperti tiga pesan dan bunyinya seolah /stop gagal.
            # Diam adalah jawaban yang benar: pembatalan sudah dikonfirmasi.
            return True
        else:
            reply = "No active task to stop."
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
                text=f"📒 Improvement: {html.escape(summary[:1500], quote=False)}",
                parse_mode="HTML",
            )
        sessions.reset(identity)
        _api_call(api, "sendMessage", chat_id=chat_id, text=_new_session_text())
        return True
    return False


def _promote_preview_text(plan: "skill_publish.PublishPlan") -> str:
    """Bangun bubble review: laporan scrub + scan + potongan isi skill."""
    lines = [f"📤 <b>Publish skill:</b> <code>{html.escape(plan.name)}</code>", ""]
    if plan.scrub_count:
        lines.append(f"🧽 <b>Scrub identitas:</b> {plan.scrub_count} penggantian")
        for sample in plan.scrub_samples[:6]:
            lines.append(f"  • {html.escape(sample)}")
    else:
        lines.append("🧽 <b>Scrub identitas:</b> tidak ada yang perlu diganti")
    lines.append("")
    if plan.findings:
        lines.append(f"🚫 <b>Scan sensitif: {len(plan.findings)} temuan — publish DIBLOKIR</b>")
        for finding in plan.findings[:12]:
            lines.append(
                f"  • L{finding.layer} {html.escape(finding.label)} "
                f"(baris {finding.line_no}): <code>{html.escape(finding.excerpt)}</code>"
            )
        lines.append("")
        lines.append("Bersihkan baris di atas dari skill dulu, lalu coba publikasi lagi.")
    else:
        lines.append("✅ <b>Scan sensitif 3-lapis:</b> BERSIH (tidak ada rahasia/identitas/infra bocor)")
        lines.append("")
        lines.append("<b>Isi skill yang akan dipublik (sudah discrub):</b>")
    return "\n".join(lines)


def _handle_promote_skill(api: str, chat_id: int, name: str) -> None:
    plan = skill_publish.prepare(name)
    if not plan.ok and plan.error:
        _api_call(api, "sendMessage", chat_id=chat_id, text=f"Failed to load skill: {plan.error}")
        return

    _api_call(api, "sendMessage", chat_id=chat_id, text=_promote_preview_text(plan), parse_mode="HTML")

    # Ada temuan → berhenti, tidak ada tombol Publish.
    if plan.findings:
        return

    # Tampilkan isi skill (dipecah aman) supaya owner bisa baca sebelum approve.
    for part in _split_message(f"```\n{plan.scrubbed}\n```"):
        _api_call(api, "sendMessage", chat_id=chat_id, text=_markdown_to_telegram_html(part), parse_mode="HTML")

    # Simpan konten yang sudah discrub agar callback bisa publish tanpa scan ulang.
    token = _stash_publish_payload(name, plan.scrubbed)
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Publish", "callback_data": f"pub:ok:{token}"},
            {"text": "❌ Batal", "callback_data": f"pub:no:{token}"},
        ]]
    }
    _api_call(
        api, "sendMessage", chat_id=chat_id,
        text=f"Publish skill <code>{html.escape(name)}</code> ke repo Zerolinear?",
        parse_mode="HTML", reply_markup=markup,
    )


# Stash payload publish (nama + konten discrub) supaya callback_data tetap pendek
# (<=64 byte): kita simpan konten di memori proses dan hanya kirim token pendek.
_PUBLISH_STASH: dict[str, tuple[str, str]] = {}
_PUBLISH_STASH_LOCK = threading.Lock()
_PUBLISH_STASH_SEQ = 0


def _stash_publish_payload(name: str, scrubbed: str) -> str:
    global _PUBLISH_STASH_SEQ
    with _PUBLISH_STASH_LOCK:
        _PUBLISH_STASH_SEQ += 1
        token = str(_PUBLISH_STASH_SEQ)
        _PUBLISH_STASH[token] = (name, scrubbed)
        # Jaga stash kecil: buang entri lama bila menumpuk.
        if len(_PUBLISH_STASH) > 32:
            for stale in list(_PUBLISH_STASH)[:-16]:
                _PUBLISH_STASH.pop(stale, None)
    return token


def _pop_publish_payload(token: str) -> tuple[str, str] | None:
    with _PUBLISH_STASH_LOCK:
        return _PUBLISH_STASH.pop(token, None)


def _handle_publish_callback(api: str, chat_id: int, message_id: int, data: str) -> None:
    """Proses tap tombol Publish/Batal. push nyata hanya di jalur 'pub:ok'."""
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    token = parts[2] if len(parts) > 2 else ""
    if action == "no":
        _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text="❌ Publish dibatalkan. Tidak ada yang di-push.")
        _pop_publish_payload(token)
        return
    payload = _pop_publish_payload(token)
    if payload is None:
        _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text="Sesi publish sudah kedaluwarsa.")
        return
    name, scrubbed = payload
    _api_call(api, "editMessageText", chat_id=chat_id, message_id=message_id, text=f"⏳ Mem-publish '{name}' ke repo…")
    try:
        result = skill_publish.publish(name, scrubbed)
    except Exception as exc:  # pragma: no cover - defensif
        result = f"GAGAL publish ({exc.__class__.__name__}): {exc}"
    _api_call(api, "sendMessage", chat_id=chat_id, text=result)


def _edit_interactive(api: str, chat_id: int, message_id: int, text: str, **extra: Any) -> None:
    """Edit pesan sebagai JAWABAN atas tap tombol — tidak boleh senyap gagal.

    Bug yang diperbaiki: `editMessageText` tidak ada di `_RETRYABLE_METHODS`, jadi
    satu ConnectionError sesaat (sering di Termux) membuat tap PERTAMA pada
    picker `/model` tampak tidak ngapa-ngapain — aes harus tap 2x (sekali untuk
    buka daftar model, sekali lagi untuk benar-benar ganti). Di sini edit diretry
    beberapa kali; kalau tetap gagal, kirim pesan baru supaya hasil tap selalu
    kelihatan alih-alih hilang tanpa jejak.
    """
    result = _api_call(
        api, "editMessageText", chat_id=chat_id, message_id=message_id,
        text=text, attempts=_INTERACTIVE_ATTEMPTS, timeout=20, **extra,
    )
    if result is None:
        _api_call(api, "sendMessage", chat_id=chat_id, text=text, **extra)


# ---------------------------------------------------------------- ask_user
#
# A tool can block mid-turn waiting for the operator. We render the question as
# its own bubble (with an inline keyboard when options were supplied) and route
# either a button tap or the next plain message back to the waiting tool.
#
# callback_data is capped at 64 bytes by Telegram, so the payload is just
# `ask:<chat_id>:<option_index>` — the question text stays in the process.

def _ask_callback_data(chat_id: int, index: int) -> str:
    return f"ask:{chat_id}:{index}"


def _handle_ask_callback(api: str, chat_id: int, message_id: int, data: str) -> None:
    """Resolve a pending ask_user question from a tapped option button."""
    parts = data.split(":")
    if len(parts) != 3:
        return
    try:
        target_chat = int(parts[1])
        index = int(parts[2])
    except ValueError:
        return
    identity = f"telegram:{target_chat}"
    chosen = interaction.answer_option(identity, index)
    if chosen is None:
        # Already answered, cancelled, or timed out — say so instead of leaving
        # a dead button that looks like the tap was ignored.
        _edit_interactive(api, chat_id, message_id, "That question is no longer waiting for an answer.")
        return
    _edit_interactive(api, chat_id, message_id, f"✅ {chosen}")


def _render_ask_question(api: str, chat_id: int, entry: Any) -> None:
    """Send the question bubble for a pending ask_user entry."""
    text = f"❓ {entry.question}"
    if entry.options:
        rows = [
            [{"text": option[:64], "callback_data": _ask_callback_data(chat_id, index)}]
            for index, option in enumerate(entry.options)
        ]
        _api_call(
            api, "sendMessage", chat_id=chat_id, text=text,
            reply_markup={"inline_keyboard": rows},
        )
    else:
        _api_call(
            api, "sendMessage", chat_id=chat_id,
            text=f"{text}\n\nReply with your answer.",
        )


def _handle_callback(api: str, callback: dict[str, Any], sessions) -> None:
    callback_id = str(callback.get("id", ""))
    data = str(callback.get("data", ""))
    message = callback.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id", 0))
    message_id = int(message.get("message_id", 0))
    # Hentikan spinner tombol SECEPATNYA lalu kerjakan sisanya. Tidak diretry:
    # callback query cepat basi ("query is too old") dan menahan di sini justru
    # menunda edit yang user tunggu.
    if callback_id:
        _api_call(api, "answerCallbackQuery", timeout=10, callback_query_id=callback_id)
    if data == "model:cancel":
        _edit_interactive(api, chat_id, message_id, "Model selection cancelled.")
        return
    if data.startswith("ask:"):
        _handle_ask_callback(api, chat_id, message_id, data)
        return
    if data.startswith("pub:"):
        _handle_publish_callback(api, chat_id, message_id, data)
        return
    providers = _configured_providers()
    if data == "provider:back":
        picker_text, markup = _provider_picker_payload(providers, _active_provider_slug(providers))
        _edit_interactive(api, chat_id, message_id, picker_text, reply_markup=markup)
        return
    if data.startswith("grp:"):
        # Pindah halaman rute pada picker model. Indeks halaman ikut di callback
        # (bukan state proses) supaya tombol tetap hidup setelah gateway restart.
        parts = data.split(":")
        try:
            provider_index = int(parts[1])
            group_index = int(parts[2])
            provider = providers[provider_index]
        except (ValueError, IndexError):
            _edit_interactive(api, chat_id, message_id, "Model selection expired. Run /model again.")
            return
        models = _discover_provider_models(provider)
        picker_text, markup = _model_picker_payload(
            models,
            provider.get("model", ""),
            provider_index,
            provider.get("name", provider["slug"]),
            group_index,
        )
        _edit_interactive(api, chat_id, message_id, picker_text, reply_markup=markup)
        return
    if data.startswith("provider:"):
        try:
            provider_index = int(data.split(":", 1)[1])
            provider = providers[provider_index]
        except (ValueError, IndexError):
            _edit_interactive(api, chat_id, message_id, "Provider selection expired. Run /model again.")
            return
        models = _discover_provider_models(provider)
        picker_text, markup = _model_picker_payload(models, provider.get("model", ""), provider_index, provider.get("name", provider["slug"]))
        _edit_interactive(api, chat_id, message_id, picker_text, reply_markup=markup)
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
        _edit_interactive(api, chat_id, message_id, "Model selection expired. Run /model again.")
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
    sessions.switch_provider(f"telegram:{chat_id}")
    _edit_interactive(api, chat_id, message_id, _model_switch_text(selected, provider), parse_mode="HTML")


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

    # 2) Rapatkan >=3 newline menjadi maksimal satu baris kosong. Ini MENJAGA
    #    pemisah paragraf (\n\n) apa adanya — hanya membuang kelebihan baris
    #    kosong. Blank line antar paragraf dari model TIDAK pernah di-collapse
    #    jadi satu \n atau spasi (spacing = keterbacaan).
    working = re.sub(r"\n{3,}", "\n\n", working)

    # 3) Beri satu baris kosong SEBELUM & SESUDAH heading agar mudah dipindai.
    working = re.sub(r"(?<=\S)\n(#{1,6}\s)", r"\n\n\1", working)          # sebelum
    working = re.sub(r"(?m)^(#{1,6}\s.+)\n(?=\S)", r"\1\n\n", working)     # sesudah

    # 4) Beri satu baris kosong saat prose langsung menempel ke awal list (biar
    #    list tidak "nabrak" kalimat pengantar di atasnya) — TAPI jangan pisahkan
    #    antar item list. Aturannya: baris NON-list diikuti baris list → blank.
    working = re.sub(
        r"(?m)^(?P<prev>(?![ \t]*(?:- |\d+\. )).*\S)\n(?=[ \t]*(?:- |\d+\. )\S)",
        r"\g<prev>\n\n",
        working,
    )
    # Blank line setelah blok list berakhir: baris list diikuti prose non-list.
    working = re.sub(
        r"(?m)^(?P<item>[ \t]*(?:- |\d+\. ).+)\n(?=(?![ \t]*(?:- |\d+\. ))[^\s#`])",
        r"\g<item>\n\n",
        working,
    )

    # 5) Buang spasi ganda di dalam prose (bukan di awal baris/indent).
    working = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", working)

    # 6) Rapikan lagi kalau langkah 3-4 memunculkan >2 newline beruntun.
    working = re.sub(r"\n{3,}", "\n\n", working)

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
        "hint": "Create a bot via @BotFather, then paste the Bot API token.",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    token = str(cfg.get("token", "")).strip()
    if not token:
        return ["Telegram token is empty"]
    if ":" not in token:
        return ["Telegram token format looks invalid"]
    profile = str(cfg.get("tool_profile", "safe"))
    if profile not in {"safe", "workspace", "full"}:
        return [f"invalid Telegram tool_profile: {profile}"]
    allowed = cfg.get("allowed", [])
    if not isinstance(allowed, list):
        return ["Telegram allowed must be a list of chat IDs"]
    if profile == "full" and not allowed:
        return ["tool_profile full requires a Telegram owner allowlist"]
    return []


def _api_call(api: str, method: str, *, timeout: int = 65, attempts: int | None = None, **params: Any) -> dict[str, Any] | None:
    """Panggil Bot API; error network tidak boleh menghentikan gateway.

    Koneksi Termux ke Telegram sering putus-putus (ConnectionError/timeout),
    yang tadinya bikin reply agent HILANG (gagal sekali → return None). Kita
    retry error jaringan sementara beberapa kali dengan backoff supaya pesan
    tetap terkirim begitu koneksi pulih. Error API non-jaringan (mis. "message
    is not modified", "query too old") TIDAK diretry — percuma.

    ``attempts`` bisa dioverride pemanggil: baris progres UI pakai attempts
    kecil + timeout pendek supaya UI tidak pernah MENAHAN loop agent (sumber
    'macet/lambat') meski jaringan lagi jelek.
    """
    if attempts is None:
        attempts = max(1, _API_RETRIES) if method in _RETRYABLE_METHODS else 1
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            response = _HTTP.post(f"{api}/{method}", json=params, timeout=timeout)
            payload = response.json()
            if response.ok and payload.get("ok"):
                return payload
            description = str(payload.get("description", "HTTP error"))[:160] if isinstance(payload, dict) else "HTTP error"
            # HTML parse error (mis. tag pre/code tak seimbang) → JANGAN sampai
            # menghilangkan pesan. Kirim ulang sekali sebagai teks polos (tanpa
            # parse_mode, entitas HTML di-escape) supaya isi tetap sampai ke user.
            if "parse entities" in description.lower() and params.get("parse_mode"):
                plain = dict(params)
                plain.pop("parse_mode", None)
                if "text" in plain:
                    # Buang tag HTML, LALU kembalikan entitas ke bentuk asli
                    # (&gt; → >, &amp; → &) supaya user tidak melihat '2&gt;'
                    # mentah saat pesan turun ke mode teks polos.
                    stripped = re.sub(r"<[^>]+>", "", str(plain["text"]))
                    plain["text"] = html.unescape(stripped)
                try:
                    retry = _HTTP.post(f"{api}/{method}", json=plain, timeout=timeout)
                    rp = retry.json()
                    if retry.ok and rp.get("ok"):
                        return rp
                except (requests.RequestException, ValueError):
                    pass
                return None
            # "message is not modified" = edit konten identik (picker dibuka
            # ulang), harmless → jangan spam log & jangan retry. Perlakukan
            # sebagai SUKSES: isi pesan sudah sesuai yang diminta, jadi pemanggil
            # interaktif tidak boleh menganggapnya gagal lalu mengirim duplikat.
            if "message is not modified" in description:
                return {"ok": True, "result": True}
            print(f"  [telegram] {method} failed: {description}", flush=True)
            return None  # error tingkat-API, retry tidak menolong
        except (requests.RequestException, ValueError) as exc:
            # Error jaringan sementara → backoff & coba lagi (kecuali attempt terakhir).
            if attempt < attempts - 1:
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            print(f"  [telegram] {method} failed: {exc.__class__.__name__} (after {attempts}x)", flush=True)
    return None


def _send_document(api: str, chat_id: int, path: Path) -> bool:
    """Kirim dokumen Telegram tanpa caption atau pesan teks kedua."""
    try:
        with path.open("rb") as document:
            response = _HTTP.post(
                f"{api}/sendDocument",
                data={"chat_id": str(chat_id)},
                files={"document": (path.name, document, "text/markdown")},
                timeout=65,
            )
        payload = response.json()
        return bool(response.ok and payload.get("ok"))
    except (OSError, requests.RequestException, ValueError):
        return False


def _split_plain(text: str, limit: int) -> list[str]:
    """Pecah prose (tanpa blok kode) di batas newline/spasi, bukan di tengah kata."""
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
    return parts


def _split_code_block(block: str, limit: int) -> list[str]:
    """Pecah SATU blok kode besar; tiap potongan tetap fence yang valid & mandiri.

    Header fence (```lang) dipertahankan di tiap potongan supaya Telegram tetap
    merender monospace — tidak pernah bocor jadi teks biasa yang wrapping aneh.
    """
    match = _FENCED_CODE_RE.match(block)
    if not match:
        return _split_plain(block, limit)
    language = match.group(1) or ""
    fence_open = f"```{language}\n"
    fence_close = "\n```"
    # Ruang untuk isi kode per potongan setelah dikurangi fence pembuka+penutup.
    inner_limit = max(1, limit - len(fence_open) - len(fence_close))
    code = match.group(2).rstrip("\n")
    lines = code.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        # +1 untuk newline penyambung.
        addition = len(line) + (1 if current else 0)
        if current and current_len + addition > inner_limit:
            chunks.append("\n".join(current))
            current, current_len = [line], len(line)
        elif len(line) > inner_limit:
            # Satu baris kode lebih panjang dari limit → potong keras per karakter.
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            for i in range(0, len(line), inner_limit):
                chunks.append(line[i:i + inner_limit])
        else:
            current.append(line)
            current_len += addition
    if current:
        chunks.append("\n".join(current))
    return [f"{fence_open}{chunk}{fence_close}" for chunk in chunks]


def _split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Pecah respons dengan menghormati blok kode — tidak pernah memotong fence.

    Bug lama: pemecahan buta bisa memotong ``` di tengah blok kode, sehingga
    potongan kehilangan penutup, gagal di-render sebagai monospace, dan muncul
    sebagai teks mentah yang wrapping berantakan di tengah token. Di sini teks
    dipecah menjadi segmen prose & blok kode utuh dulu, baru digabung sampai
    mendekati limit; blok kode raksasa dipecah tapi tetap fence yang valid.
    """
    if len(text) <= limit:
        return [text]

    # 1) Pisahkan menjadi segmen: prose vs blok kode utuh (urutan dipertahankan).
    segments: list[str] = []
    last = 0
    for match in _FENCED_CODE_RE.finditer(text):
        if match.start() > last:
            segments.append(text[last:match.start()])
        segments.append(match.group(0))
        last = match.end()
    if last < len(text):
        segments.append(text[last:])

    # 2) Pecah tiap segmen yang kegedean; segmen kecil dibiarkan utuh.
    pieces: list[str] = []
    for segment in segments:
        if not segment.strip():
            continue
        if len(segment) <= limit:
            pieces.append(segment.strip("\n"))
        elif segment.startswith("```"):
            pieces.extend(_split_code_block(segment, limit))
        else:
            pieces.extend(_split_plain(segment, limit))

    # 3) Gabung potongan berurutan selama muat, TAPI jangan gabung blok kode
    #    dengan prose (biar tiap bubble tetap rapi dan fence utuh).
    parts: list[str] = []
    for piece in pieces:
        is_code = piece.startswith("```")
        if (
            parts
            and not is_code
            and not parts[-1].startswith("```")
            and len(parts[-1]) + 2 + len(piece) <= limit
        ):
            parts[-1] = f"{parts[-1]}\n\n{piece}"
        else:
            parts.append(piece)
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


def _update_trace(update: dict[str, Any]) -> str:
    """Ringkasan satu update untuk log — cukup untuk diagnosa, tanpa isi pesan.

    Isi pesan SENGAJA tidak dicatat: log gateway bukan arsip percakapan, dan
    menuliskannya membuat file log jadi salinan chat (termasuk hal sensitif).
    Yang dicatat hanya yang dibutuhkan saat menelusuri 'kenapa bot diam':
    jenis update, chat/user-nya, dan bentuk muatannya (teks/command/media).
    """
    callback = update.get("callback_query") or {}
    if callback:
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id", "?")
        user_id = (callback.get("from") or {}).get("id", "?")
        # callback_data aman dicatat: itu identitas tombol buatan kita sendiri
        # (mis. `grp:0:1`), bukan teks yang user tulis.
        return f"callback chat={chat_id} user={user_id} data={str(callback.get('data', ''))[:32]}"
    message = update.get("message") or {}
    if not message:
        return f"other keys={','.join(k for k in update if k != 'update_id')[:60]}"
    chat = message.get("chat") or {}
    chat_id = chat.get("id", "?")
    user_id = (message.get("from") or {}).get("id", "?")
    text = str(message.get("text") or "").strip()
    if text.startswith("/"):
        kind = f"command={text.split()[0][:24]}"  # nama command saja, tanpa argumen
    elif text:
        kind = f"text len={len(text)}"
    elif message.get("photo"):
        kind = "photo"
    elif message.get("document"):
        kind = "document"
    elif message.get("voice") or message.get("audio"):
        kind = "audio"
    elif message.get("video") or message.get("video_note"):
        kind = "video"
    else:
        kind = "empty"
    return f"message chat={chat_id} user={user_id} {kind}"


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
        return "/status · /models · /model <id> · /version · /update · /new · /restart · /stop · /logs"
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
        # Ganti model = ganti otak saja; konteks percakapan tetap dijaga.
        if hasattr(sessions, "switch_provider"):
            sessions.switch_provider(identity)
        return f"Model updated: `{args}`. Conversation context preserved."
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


# Direktori tempat media masuk (foto/voice/video) disimpan agar tool bisa membacanya.
MEDIA_INBOX = config.DATA_DIR / "media-inbox"


def _download_media_file(api: str, token: str, file_id: str, suffix: str) -> tuple[Path | None, str | None]:
    """Unduh media (foto/voice/video) Telegram ke media-inbox lokal.

    Mengembalikan (path, None) sukses atau (None, pesan_error).
    """
    meta = _api_call(api, "getFile", timeout=20, file_id=file_id)
    file_path = str((meta or {}).get("result", {}).get("file_path") or "")
    if not file_path:
        return None, "Could not get Telegram file metadata."
    try:
        response = requests.get(FILE_API_TEMPLATE.format(token=token, file_path=file_path), timeout=60)
        if not response.ok or len(response.content) > TELEGRAM_TEXT_FILE_LIMIT:
            return None, "Could not download the media safely."
    except requests.RequestException as exc:
        return None, f"Could not download the media: {exc.__class__.__name__}."
    try:
        MEDIA_INBOX.mkdir(parents=True, exist_ok=True)
        ext = Path(file_path).suffix or suffix
        dest = MEDIA_INBOX / f"{file_id[:24]}{ext}"
        dest.write_bytes(response.content)
    except OSError as exc:
        return None, f"Could not save the media: {exc.__class__.__name__}."
    return dest, None


def _build_image_prompt(path: Path, caption: str = "") -> str:
    """Prompt agen untuk gambar: arahkan pakai analyze_media pada path lokal."""
    ask = caption.strip() or "Describe what you see and anything notable."
    return (
        f"User sent an image via Telegram, saved at `{path}`. "
        f"Use the analyze_media tool with path_or_url=\"{path}\" to look at it, "
        f"then answer the user. Request/caption: {ask}"
    )


def _build_media_notice_prompt(kind: str, path: Path, caption: str = "") -> str:
    """Prompt agen untuk audio/video: jelaskan jalur transkrip/ekstraksi frame."""
    ask = caption.strip() or "(no caption)"
    return (
        f"User sent a {kind} via Telegram, saved at `{path}`. You can inspect it with "
        f"the analyze_media tool (path_or_url=\"{path}\") which will explain the correct "
        f"handling ({kind} needs transcription or frame extraction, not direct vision). "
        f"Caption/request: {ask}"
    )


def _send_agent_reply(api: str, sessions, *, chat_id: int, identity: str, text: str, tool_profile: str, reply_to_message_id: int | None = None) -> None:
    _api_call(api, "sendChatAction", chat_id=chat_id, action="typing",
              timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS)
    done = threading.Event()
    live = _LiveStatus(api, chat_id, model=getattr(config, "MODEL", ""))
    heartbeat = _start_working_heartbeat(api, chat_id, done, status=live)
    # CATATAN: streaming/live-edit token (edit satu bubble berulang) DIMATIKAN.
    # Di Android bubble yang di-edit terus makin berat/lag makin panjang, dan
    # markdown setengah jadi (##) sempat keliatan mentah. Alur yang diminta
    # persis Zeline: kerjain sambil kirim pesan SATU-SATU yang sudah
    # rapi — narasi & jawaban akhir masing-masing jadi bubble utuh sendiri
    # (tidak ada edit-in-place), diselingi feed aktivitas tool.
    def on_tool(_name, _args):
        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing",
                  timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS)
        # Tambahkan satu baris ringkas ke feed live, bukan pesan baru.
        # _safe_progress_line menjaga tag HTML (pre/code) tetap seimbang saat
        # dipangkas — mencegah editMessageText gagal "Can't find end tag pre".
        live.add(_safe_progress_line(_tool_progress_text(_name, _args)))
        # ask_user memblokir sampai user menjawab, jadi pertanyaannya harus
        # SUDAH terkirim sebelum tool-nya menunggu. Kirim di sini, tepat setelah
        # tool call terlihat: kalau menunggu on_tool_result, pertanyaan baru
        # muncul setelah jawabannya masuk — terlambat dan bikin user bingung.
        if _name == "ask_user":
            entry = None
            # Tool berjalan di thread lain; beri jeda singkat sampai entry
            # terdaftar, lalu render. Tanpa polling kecil ini kita bisa
            # membaca state sebelum interaction.ask() mendaftarkannya.
            for _ in range(20):
                entry = interaction.pending(identity)
                if entry is not None:
                    break
                time.sleep(0.05)
            if entry is not None:
                live.detach()
                _render_ask_question(api, chat_id, entry)

    def on_tool_result(_name, _args, result):
        result_text = _tool_result_text(_name, _args, result)
        if result_text:
            live.add(_safe_progress_line(result_text))
        # Setelah tool selesai, kita kembali menunggu respons model.
        live.set_waiting()

    def on_iteration(current, maximum):
        # Awal tiap iterasi = mulai menunggu respons provider. Simpan nomor
        # langkah supaya header status bisa menunjukkan 'step 4/20'.
        live.set_iteration(current, maximum)
        live.set_waiting()

    def on_narration(sentence: str):
        # Kalimat rencana/temuan model yang menyertai tool call → dikirim
        # sebagai bubble chat UTUH tersendiri SEBELUM tool jalan. Ini yang
        # bikin alur kebaca hidup & "satset": [penjelasan] → [tool feed] →
        # [penjelasan] → [jawaban], tiap pesan rapi dan dikirim sekali (bukan
        # di-edit live). Selesaikan dulu bubble progres berjalan biar narasi
        # baru tampil di bawah aktivitas tool sebelumnya, bukan menimpanya.
        sentence = sentence.strip()
        if not sentence:
            return
        live.detach()
        for part in _split_message(sentence):
            _api_call(
                api, "sendMessage", chat_id=chat_id,
                text=_markdown_to_telegram_html(part), parse_mode="HTML",
            )
        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing",
                  timeout=_PROGRESS_TIMEOUT, attempts=_PROGRESS_ATTEMPTS)

    ok = False
    try:
        reply = sessions.send(
            identity=identity,
            text=text,
            tool_profile=tool_profile,
            on_tool=on_tool,
            on_tool_result=on_tool_result,
            on_iteration=on_iteration,
            on_narration=on_narration,
        )
        ok = True
    except ZelineError as exc:
        reply = _format_agent_error(str(exc))
    except Exception as exc:
        print(f"  [telegram] unhandled agent error: {exc.__class__.__name__}: {exc}", flush=True)
        reply = f"🪫 Zeline hit a problem — an unexpected internal error ({exc.__class__.__name__}). Please try again in a moment."
    finally:
        done.set()
        heartbeat.join(timeout=0.2)
        if ok:
            # Kunci bubble progres sebagai catatan alur (tidak dihapus), lalu
            # kirim jawaban final sebagai pesan baru terpisah.
            live.finalize()
        else:
            live.clear()  # error/batal: buang bubble agar tidak menyisakan sampah
    # /stop sudah mengirim SATU konfirmasi sendiri ("❄️ Stopped — <judul>").
    # Agent mengembalikan sentinel "Stopped." untuk turn yang sama, jadi
    # mengirimnya berarti dua pesan untuk satu pembatalan — dan refleksi di
    # bawah bisa menambah pesan ketiga. Turn yang dibatalkan berhenti di sini:
    # tidak ada balasan, tidak ada refleksi, tidak ada bubble sisa.
    if isinstance(reply, str) and reply.strip() == _CANCELLED_SENTINEL and _consume_stop(identity):
        live.clear()
        return
    # Jawaban final SELALU dikirim sebagai pesan baru yang utuh & rapi (bukan
    # edit-in-place). Panjang → dipecah aman multi-part lewat _split_message.
    # Bubble PERTAMA di-reply ke pesan user (reply_to_message_id) supaya jelas
    # balasan ini untuk pertanyaan yang mana — penting saat user mengirim
    # beberapa pertanyaan dalam bubble terpisah. Part berikutnya tanpa reply
    # (biar rantai jawaban tidak menumpuk quote berulang).
    first_part = True
    for part in _split_message(reply):
        extra: dict[str, Any] = {}
        if first_part and reply_to_message_id:
            extra["reply_to_message_id"] = reply_to_message_id
            extra["allow_sending_without_reply"] = True
        _api_call(
            api,
            "sendMessage",
            chat_id=chat_id,
            text=_markdown_to_telegram_html(part),
            parse_mode="HTML",
            **extra,
        )
        first_part = False

    # Self-improvement: setelah turn berbobot (banyak tool), jalankan refleksi di
    # background agar tidak menahan balasan. reflect() sendiri menjaga ambang
    # (profile full + cukup tool call) dan hanya kirim pesan bila benar-benar
    # menyimpan/memperbaiki skill — jadi ini yang bikin Zeline "sering
    # Self-improvement" seperti diminta, tanpa nyampah di sesi ringan.
    if ok:
        def _reflect_bg():
            try:
                summary = sessions.reflect(identity)
            except Exception:
                summary = None
            if summary:
                _api_call(
                    api, "sendMessage", chat_id=chat_id,
                    text=f"📒 Improvement: {html.escape(summary[:1500], quote=False)}",
                    parse_mode="HTML",
                )
        threading.Thread(target=_reflect_bg, daemon=True, name=f"zeline-reflect-{chat_id}").start()


def _start_agent_reply(api: str, sessions, *, chat_id: int, identity: str, text: str, tool_profile: str, reply_to_message_id: int | None = None) -> threading.Thread:
    """Jalankan turn di worker agar polling tetap menerima /stop dan /steer.

    Kirim 'typing…' SEKETIKA (sinkron, dari loop polling) sebelum worker dimulai.
    Sebelumnya sendChatAction baru dipanggil di dalam worker setelah session
    di-load, jadi ada jeda terlihat 'diam' sebelum 'sedang mengetik' muncul.
    Satu round-trip sendChatAction sangat cepat (~ratusan ms) dan langsung
    memberi feedback bahwa bot menerima pesan.

    ``reply_to_message_id`` dipakai agar bubble jawaban final nempel (quote) ke
    pesan user — jelas balasan untuk pertanyaan yang mana saat ada beberapa.
    """
    try:
        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing", timeout=10)
    except Exception:
        pass
    worker = threading.Thread(
        target=_send_agent_reply,
        kwargs={
            "api": api,
            "sessions": sessions,
            "chat_id": chat_id,
            "identity": identity,
            "text": text,
            "tool_profile": tool_profile,
            "reply_to_message_id": reply_to_message_id,
        },
        name=f"zeline-telegram-{chat_id}",
        daemon=True,
    )
    worker.start()
    return worker


def _dispatch_update(
    api: str,
    token: str,
    sessions,
    update: dict[str, Any],
    *,
    allowed: list[Any],
    tool_profile: str,
    stop_event,
) -> None:
    """Proses satu update Telegram (message / callback_query).

    Dipisah dari loop polling supaya pemanggil bisa membungkusnya dengan
    try/except: satu update bermasalah tidak boleh menjatuhkan loop (yang dulu
    bikin thread gateway mati diam dan butuh SIGKILL untuk restart).
    """
    callback = update.get("callback_query") or {}
    message = update.get("message") or {}
    if callback:
        callback_message = callback.get("message") or {}
        callback_chat_id = (callback_message.get("chat") or {}).get("id")
        callback_user_id = (callback.get("from") or {}).get("id")
        if callback_chat_id is not None and callback_user_id is not None and _allowed(int(callback_user_id), allowed):
            # SEMUA proses callback (termasuk answerCallbackQuery) dijalankan di
            # thread terpisah supaya loop polling TIDAK PERNAH ter-blok oleh
            # round-trip HTTP ke Telegram (yang bisa lambat dari Termux).
            threading.Thread(
                target=_handle_callback,
                args=(api, dict(callback), sessions),
                daemon=True,
                name="zeline-callback",
            ).start()
        elif callback.get("id"):
            _api_call(api, "answerCallbackQuery", callback_query_id=str(callback["id"]), text="Access denied.", show_alert=True)
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    incoming_message_id = int(message.get("message_id") or 0) or None
    text = str(message.get("text") or "").strip()
    caption = str(message.get("caption") or "").strip()
    document = message.get("document") or {}
    photos = message.get("photo") or []
    voice = message.get("voice") or message.get("audio") or {}
    video = message.get("video") or message.get("video_note") or {}

    if chat_id is None:
        return
    chat_id_int = int(chat_id)
    identity = f"telegram:{chat_id_int}"

    if not _allowed(chat_id_int, allowed):
        _api_call(api, "sendMessage", chat_id=chat_id_int, text="Access to this bot is not permitted yet.")
        return

    if text:
        if text.startswith("/"):
            handled = _handle_command_update(
                api, text, sessions, identity, chat_id_int,
                stop_event=stop_event, tool_profile=tool_profile, allowed=allowed,
                message_id=int(message.get("message_id") or 0),
            )
            if not handled:
                command_reply = _handle_command(text, sessions, identity, stop_event=stop_event)
                _api_call(api, "sendMessage", chat_id=chat_id_int, text=command_reply or "Unknown command. Use /start.")
        else:
            # A tool may be blocked on ask_user. The user's next plain message is
            # that ANSWER, not a new request — routing it to a fresh turn would
            # both strand the waiting tool and run the wrong thing. Commands are
            # deliberately checked first so /stop still escapes a question.
            if interaction.answer(identity, text):
                return
            _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=text, tool_profile=tool_profile, reply_to_message_id=incoming_message_id)
    elif document:
        filename = _document_filename(document)
        file_content, error = _download_document(api, token, document)
        if error:
            _api_call(api, "sendMessage", chat_id=chat_id_int, text=error)
            return
        file_text, error = _extract_document_text(filename, file_content or b"", str(document.get("mime_type") or ""))
        if error:
            _api_call(api, "sendMessage", chat_id=chat_id_int, text=error)
            return
        prompt = _build_document_prompt(filename, file_text or "", caption)
        _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=prompt, tool_profile=tool_profile, reply_to_message_id=incoming_message_id)
    elif photos:
        # Ambil resolusi terbesar (elemen terakhir) untuk kualitas vision.
        largest = photos[-1] if isinstance(photos, list) else {}
        dest, error = _download_media_file(api, token, str(largest.get("file_id") or ""), ".jpg")
        if error or dest is None:
            _api_call(api, "sendMessage", chat_id=chat_id_int, text=error or "Could not read the image.")
            return
        prompt = _build_image_prompt(dest, caption)
        _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=prompt, tool_profile=tool_profile, reply_to_message_id=incoming_message_id)
    elif voice:
        dest, error = _download_media_file(api, token, str(voice.get("file_id") or ""), ".ogg")
        if error or dest is None:
            _api_call(api, "sendMessage", chat_id=chat_id_int, text=error or "Could not read the audio.")
            return
        prompt = _build_media_notice_prompt("audio", dest, caption)
        _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=prompt, tool_profile=tool_profile, reply_to_message_id=incoming_message_id)
    elif video:
        dest, error = _download_media_file(api, token, str(video.get("file_id") or ""), ".mp4")
        if error or dest is None:
            _api_call(api, "sendMessage", chat_id=chat_id_int, text=error or "Could not read the video.")
            return
        prompt = _build_media_notice_prompt("video", dest, caption)
        _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=prompt, tool_profile=tool_profile, reply_to_message_id=incoming_message_id)
    elif caption:
        _start_agent_reply(api, sessions, chat_id=chat_id_int, identity=identity, text=caption, tool_profile=tool_profile, reply_to_message_id=incoming_message_id)
    else:
        _api_call(
            api,
            "sendMessage",
            chat_id=chat_id_int,
            text="This message could not be processed. Send text, an image, voice/video, or a .txt/.md file.",
        )


def _verify_token(api: str) -> tuple[str | None, str]:
    """Verifikasi token saat startup. Return (username, alasan-gagal).

    Bug yang diperbaiki: dulu `getMe` dipanggil sekali (`attempts=1`), dan SATU
    ReadTimeout — biasa saja di Termux, latensi terukur 1.5-15.8s — bikin gateway
    langsung berhenti dengan "token could not be verified". Efeknya bot mati
    total dan `/model` tidak dijawab sama sekali, padahal tokennya valid.

    Sekarang: network error diretry dengan backoff, dan gateway hanya menolak
    jalan kalau Telegram BENAR-BENAR menolak tokennya (401/404). Itu pembedaan
    yang penting — "jaringan lagi jelek" bukan "token salah".
    """
    last_error = "no response"
    for attempt in range(_STARTUP_VERIFY_ATTEMPTS):
        try:
            response = _HTTP.post(f"{api}/getMe", json={}, timeout=_STARTUP_VERIFY_TIMEOUT)
        except (requests.RequestException, ValueError) as exc:
            last_error = f"network: {exc.__class__.__name__}"
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.ok and payload.get("ok"):
                return str((payload.get("result") or {}).get("username", "?")), ""
            description = str(payload.get("description", f"HTTP {response.status_code}"))[:160]
            # Penolakan otentikasi nyata → berhenti sekarang, retry tidak menolong.
            if response.status_code in (401, 404):
                return None, f"rejected by Telegram: {description}"
            last_error = description
        if attempt < _STARTUP_VERIFY_ATTEMPTS - 1:
            delay = min(2.0 * (attempt + 1), 8.0)
            print(f"  [telegram] getMe {last_error}; retry in {delay:.0f}s", flush=True)
            time.sleep(delay)
    return None, last_error


def start(sessions, cfg: dict[str, Any], stop_event) -> None:
    token = str(cfg["token"]).strip()
    api = API_TEMPLATE.format(token=token)
    allowed = cfg.get("allowed", [])
    tool_profile = str(cfg.get("tool_profile", "safe"))

    username, failure = _verify_token(api)
    if username is None:
        print(f"  [telegram] token could not be verified ({failure}); gateway stopped.", flush=True)
        return
    print(f"  [telegram] @{username} connected via polling", flush=True)
    _api_call(api, "setMyCommands", commands=_telegram_commands())

    offset = _load_offset()
    # Backoff adaptif + self-heal: loop polling TIDAK BOLEH mati permanen.
    # Dulu exception di handler per-update (di luar try getUpdates) bisa
    # menembus keluar loop → seluruh start() crash → thread gateway mati diam
    # (proses induk masih hidup, butuh SIGKILL). Sekarang: (a) tiap update
    # dibungkus try/except sendiri, (b) getUpdates yang gagal beruntun pakai
    # backoff yang naik lalu reset saat pulih, (c) 409 Conflict (instance
    # dobel) ditangani spesifik, (d) heartbeat log tiap ~5 menit supaya
    # 'diam' selalu kelihatan di log, bukan misteri.
    consecutive_errors = 0
    last_heartbeat = time.monotonic()
    # Kapan padamnya jaringan mulai (None = sedang sehat). Dipakai untuk
    # melaporkan durasi padam + baris PULIH, bukan cuma nomor retry.
    outage_since: float | None = None
    while not stop_event.is_set():
        try:
            now = time.monotonic()
            if now - last_heartbeat >= 300:
                print(f"  [telegram] polling alive (offset={offset})", flush=True)
                last_heartbeat = now
            try:
                response = requests.get(
                    f"{api}/getUpdates",
                    # Long-poll dipersingkat (10s) agar shutdown responsif: begitu
                    # stop_event di-set, loop keluar dalam <=10s alih-alih menggantung
                    # sampai 25-35s (penyebab `gateway stop` sering nyangkut lalu
                    # butuh SIGKILL). Read-timeout diberi margin di atas long-poll.
                    params={"offset": offset, "timeout": 10, "allowed_updates": json.dumps(["message", "callback_query"])},
                    timeout=20,
                )
                payload = response.json()
                if not response.ok or not payload.get("ok"):
                    description = str(payload.get("description", f"HTTP {response.status_code}"))[:160]
                    # 409 Conflict = ada instance lain yang ikut polling token yang
                    # sama. Log jelas + backoff supaya tidak spam & tidak spin CPU;
                    # begitu instance duplikat mati, polling pulih sendiri.
                    if response.status_code == 409 or "conflict" in description.lower():
                        # Kunci proses (gateway.lock) mencegah duplikat di mesin
                        # INI; 409 yang masih lolos berarti poller lain di mesin
                        # atau host lain memakai token yang sama. Sebutkan itu
                        # supaya user tidak mengira bot-nya rusak.
                        print(
                            "  [telegram] 409 Conflict — another poller is using this bot token "
                            "(another device/host, or a webhook). Only one may poll at a time; waiting…",
                            flush=True,
                        )
                        consecutive_errors += 1
                        stop_event.wait(min(3 + consecutive_errors, 15))
                        continue
                    print(f"  [telegram] getUpdates failed: {description}", flush=True)
                    consecutive_errors += 1
                    stop_event.wait(min(3 + consecutive_errors, 15))
                    continue
            except requests.Timeout:
                consecutive_errors = 0  # long-poll timeout = normal, bukan error
                continue
            except (requests.RequestException, ValueError) as exc:
                # Error jaringan sementara (Termux drop): backoff naik lalu reset
                # saat pulih. TIDAK PERNAH keluar loop → bot auto-recover sendiri.
                consecutive_errors += 1
                if outage_since is None:
                    outage_since = time.monotonic()
                # JANGAN cetak tiap percobaan. Padamnya jaringan Termux bisa
                # ratusan retry beruntun ("retry #233") yang menenggelamkan baris
                # penting lain di log dan menggelembungkan file. Cetak error
                # pertama, lalu ringkasan berkala saja — informasinya sama, tanpa
                # spam. Yang benar-benar dibutuhkan justru baris PULIH di bawah.
                if consecutive_errors == 1 or consecutive_errors % 20 == 0:
                    down_for = time.monotonic() - outage_since
                    print(
                        f"  [telegram] polling error: {exc.__class__.__name__} "
                        f"(retry #{consecutive_errors}, down {down_for:.0f}s) — "
                        "bot cannot receive messages while this lasts",
                        flush=True,
                    )
                stop_event.wait(min(3 + consecutive_errors, 15))
                continue

            # getUpdates sukses. Bila baru saja padam, catat PULIHNYA + berapa
            # lama: inilah yang membuat 'tadi Zeline diam' bisa dijelaskan dari
            # log alih-alih ditebak dari pergerakan offset.
            if outage_since is not None:
                print(
                    f"  [telegram] polling recovered after {time.monotonic() - outage_since:.0f}s "
                    f"({consecutive_errors} failed attempts); messages sent during the outage arrive now",
                    flush=True,
                )
                outage_since = None
            consecutive_errors = 0  # getUpdates sukses → reset backoff

        except Exception as exc:  # jaring pengaman terakhir: apa pun jangan bunuh loop
            print(f"  [telegram] polling loop recovered from: {exc.__class__.__name__}: {exc}", flush=True)
            stop_event.wait(2)
            continue

        for update in payload.get("result", []):
            update_id = int(update.get("update_id", -1))
            # Jejak satu baris per update MASUK. Tanpa ini, log hanya berisi
            # error, jadi saat bot "diam" tidak ada cara membedakan (a) update
            # tidak pernah sampai dari (b) sampai tapi gagal dibalas — dulu
            # harus ditebak lewat pergerakan offset. Ringkas & tanpa isi pesan
            # supaya log tidak jadi arsip percakapan.
            print(f"  [telegram] update {update_id} in: {_update_trace(update)}", flush=True)
            # Bungkus SETIAP update: satu pesan bermasalah tidak boleh menjatuhkan
            # loop polling. Offset tetap maju di finally supaya update rusak tidak
            # diproses ulang tanpa henti (poison message).
            try:
                _dispatch_update(api, token, sessions, update, allowed=allowed, tool_profile=tool_profile, stop_event=stop_event)
            except Exception as exc:
                print(f"  [telegram] update {update_id} skipped: {exc.__class__.__name__}: {exc}", flush=True)
            finally:
                offset = max(offset, update_id + 1)
                _save_offset(offset)

    print("  [telegram] stopped", flush=True)
