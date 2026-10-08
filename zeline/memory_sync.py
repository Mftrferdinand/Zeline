"""Sinkronisasi otomatis konektor eksternal ke memory per identitas.

Ide sederhana: hal-hal yang terjadi di luar chat — email baru, acara
kalender, aktivitas GitHub — adalah fakta tentang hidup operator yang
layak diingat, tapi operator tidak mau mengetiknya satu per satu. Modul
ini menarik aktivitas TERBARU dari tiap konektor, mengubahnya menjadi
fakta ringkas yang DETERMINISTIK (bukan ringkasan LLM — formatnya selalu
sama, jadi tidak ada token yang terbakar untuk hal sepele), dan
menyimpannya ke :class:`~zeline.memory.MemoryStore` milik identity yang
sama.

Kenapa deterministik, bukan LLM:

- **Murah.** Sinkronisasi jalan berkala di background; setiap byte ringkasan
  yang dibayar ke model adalah biaya operasional, bukan nilai.
- **Stabil.** Format ``"Dari <sender>: <subject> — <snippet>"`` bisa diuji
  dengan mock tanpa model, dan hasilnya tidak pernah "berkreasi".

Idempotensi dijaga lewat WATERMARK per identity+source
(``~/.zeline/memory-sync/<hash>_<source>.json``): ID yang sudah diproses
(message ID Gmail, baris acara kalender, nomor issue/PR GitHub) dicatat,
jadi sync kedua tidak menarik ulang yang sama. Pola penyimpanan mengikuti
``zeline.tasks``: nama file di-hash (identity tidak bocor lewat nama
file), direktori 0700, file 0600, tulis atomik.

Isolasi error: setiap source dibungkus try/except sendiri di
:func:`sync_all`. Satu konektor yang mati (token kedaluwarsa, jaringan
putus) dicatat di ``errors`` ringkasan — source lain tetap jalan.

Konfigurasi lewat environment:

- ``MEMORY_SYNC_ENABLED`` (default ``"true"``). Bila false, :func:`sync_all`
  tidak memanggil konektor sama sekali.
- ``MEMORY_SYNC_INTERVAL_HOURS`` (default ``"6"``). Hanya diekspor sebagai
  konstanta :data:`SYNC_INTERVAL_HOURS` untuk penjadwal di luar modul ini —
  modul ini TIDAK mendaftarkan cron job dan TIDAK menyentuh scheduler.
- ``MEMORY_SYNC_GITHUB_REPOS`` (default kosong = GitHub di-skip total).
  Daftar ``owner/repo`` dipisah koma.

Keamanan: sinkronisasi hanya MEMBACA (gmail_search/gmail_read,
calendar_list, list_issues/list_prs). Tidak ada kirim email, tidak ada
tulis issue, tidak ada mutasi apa pun.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any

from zeline import config
from zeline.memory import MemoryStore

#: Confidence untuk semua fakta hasil sync: lebih rendah dari fakta yang
#: dinyatakan user (1.0) karena ini diekstrak otomatis, tapi lebih tinggi
#: dari refleksi (0.6) karena berasal dari data eksternal yang konkret.
SYNC_CONFIDENCE = 0.7

#: Panjang maksimum cuplikan (snippet) email yang masuk ke fakta.
SNIPPET_MAX_CHARS = 300

#: Batas berapa banyak ID yang disimpan di satu file watermark. Tanpa batas,
#: file tumbuh selamanya seiring tahun berjalan; yang terlama dibuang dulu
#: karena ID lama tidak akan muncul lagi di hasil "terbaru".
MAX_SEEN_IDS = 2_000


def _interval_hours() -> int:
    """Baca MEMORY_SYNC_INTERVAL_HOURS, fallback 6 bila hilang/rusak.

    Diekspor sebagai konstanta untuk dibaca penjadwal; tidak dipakai untuk
    menjadwal apa pun di modul ini.
    """
    try:
        value = int(os.environ.get("MEMORY_SYNC_INTERVAL_HOURS", "6") or "6")
    except (TypeError, ValueError):
        return 6
    return max(1, value)


SYNC_INTERVAL_HOURS = _interval_hours()


def _enabled() -> bool:
    """Master switch. Dibaca per panggilan supaya test bisa toggle per kasus."""
    return (
        os.environ.get("MEMORY_SYNC_ENABLED", "true").strip().lower()
        in ("1", "true", "yes", "on")
    )


def _github_repos() -> list[str]:
    """Repo yang dikonfigurasi untuk sync: list "owner/repo", kosong bila tidak ada."""
    raw = os.environ.get("MEMORY_SYNC_GITHUB_REPOS", "") or ""
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


#: Lock per identity DALAM satu proses mengikuti pola zeline.memory._lock_for:
#: dua sync_all konkuren untuk identity yang sama tidak boleh interleave
#: read-modify-write watermark (entri seen bisa hilang).
#: BATASAN JUJUR: sama seperti goals — lintas-proses, dua penulis yang
#: bersamaan tetap bisa lost-update (last-writer-wins); rename atomik hanya
#: mencegah file setengah-tulis, bukan menggabungkan perubahan. Dampaknya
#: terbatas: watermark hanya berisi himpunan ID yang SUDAH diproses, jadi
#: lost-update paling buruk = beberapa ID diproses ulang di run berikutnya
#: (duplikat), bukan data yang hilang.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(identity: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(identity)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[identity] = lock
        return lock


def _per_identity_lock(func):  # type: ignore[no-untyped-def]
    """Decorator: jalankan fungsi sync dengan lock per identity."""

    @wraps(func)
    def wrapper(identity: str, *args: Any, **kwargs: Any) -> Any:
        with _lock_for(identity):
            return func(identity, *args, **kwargs)

    return wrapper


# --- Watermark ------------------------------------------------------------

#: Pola pengirim yang jelas-jelas noise. ``noreply``/``no-reply``/
#: ``donotreply``/``newsletter`` hanya cocok sebagai SELURUH local-part di
#: AWAL alamat — dijangkar ke awal string, atau ke setelah spasi/``<`` bila
#: header From memakai display-name (mis. ``"Jane" <noreply@corp.id>``) —
#: supaya tidak false-positive pada alamat manusia yang kebetulan mengandung
#: substring tersebut (mis. ``jane.noreply@corp.id`` atau ``annnoreply@mail.id``
#: tetap lolos). ``notification`` SENGAJA tidak difilter — notifikasi (mis.
#: GitHub) bisa membawa info aktivitas yang relevan untuk memory.
#:
#: BATASAN DIKETAHUI (tradeoff anchoring): pengirim otomatis yang local-part-nya
#: tidak diawali kata noise tetap lolos dan jadi fakta, mis.
#: ``.noreply@x.id`` (diawali titik), ``bounce-noreply@mail.x.id``,
#: ``weekly-newsletter@blog.id``. Ini disengaja — anchoring sempit lebih aman
#: daripada filter longgar yang menghapus email manusia (kasus nyata:
#: ``rani@newsletter.id`` pernah terfilter + ter-watermark karena ``\bnewsletter\b``
#: cocok di domain, sehingga tidak pernah dicoba lagi).
_NOISE_SENDER_RE = re.compile(
    r"(?:^|[\s<])(?:noreply|no-?reply|donotreply|newsletter)@",
    re.IGNORECASE,
)


def _sync_dir() -> Path:
    return config.DATA_DIR / "memory-sync"


def _watermark_key(identity: str, source: str) -> str:
    digest = hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]
    safe_source = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-") or "misc"
    return f"{digest}_{safe_source}"


def _watermark_path(identity: str, source: str) -> Path:
    return _sync_dir() / f"{_watermark_key(identity, source)}.json"


def _read_seen(identity: str, source: str) -> set[str]:
    """ID yang sudah diproses untuk identity+source ini. Korup/hilang = set kosong."""
    path = _watermark_path(identity, source)
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    seen = raw.get("seen") if isinstance(raw, dict) else None
    if not isinstance(seen, list):
        return set()
    return {str(item) for item in seen if str(item).strip()}


def _write_seen(identity: str, source: str, seen: set[str]) -> None:
    """Tulis ulang watermark atomis (0600), pola yang sama seperti zeline.tasks."""
    directory = _sync_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Direktori watermark tidak bisa dibuat: sync tetap dianggap sukses,
        # tapi watermark tidak dicatat — run berikutnya memproses ulang ID
        # yang sama. Duplikat lebih aman daripada sync yang crash.
        return
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    items = sorted(seen)
    if len(items) > MAX_SEEN_IDS:
        # Urutan sort tidak kronologis, tapi ID lama tidak muncul lagi di
        # hasil "terbaru" — membuang yang mana pun aman secara praktis.
        items = items[-MAX_SEEN_IDS:]
    payload = {"seen": items, "updated_at": time.time()}
    target = _watermark_path(identity, source)
    temporary = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)


def _resolve_connector(connector: Any, connector_id: str) -> Any:
    """Pakai connector yang diberikan, atau ambil dari registry bila None."""
    if connector is not None:
        return connector
    from zeline.connectors import get

    resolved = get(connector_id)
    if resolved is None:
        raise RuntimeError(f"connector '{connector_id}' is not registered")
    return resolved


def _clean(text: str) -> str:
    """Rapikan whitespace: collapse jadi satu spasi, buang tepi."""
    return " ".join((text or "").split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


# --- Gmail ----------------------------------------------------------------

def _parse_search_line(line: str) -> tuple[str, str, str, str] | None:
    """Pecah satu baris hasil gmail_search: ``id | date | from | subject``."""
    parts = [piece.strip() for piece in line.split(" | ", 3)]
    if len(parts) != 4 or not parts[0]:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def _parse_read_message(text: str) -> tuple[str, str, str, str]:
    """Pecah hasil gmail_read: baris Subject/From/Date lalu badan pesan."""
    subject = sender = date = ""
    body = text or ""
    head, _, rest = body.partition("\n\n")
    body = rest
    for line in head.splitlines():
        name, _, value = line.partition(":")
        name = name.strip().lower()
        if name == "subject":
            subject = value.strip()
        elif name == "from":
            sender = value.strip()
        elif name == "date":
            date = value.strip()
    return subject, sender, date, body


def _sender_is_noise(sender: str) -> bool:
    return bool(_NOISE_SENDER_RE.search(sender or ""))


def _gmail_fact(sender: str, subject: str, body: str) -> str:
    """Fakta deterministik satu email. Format selalu sama, tanpa LLM.

    Bila badan kosong (email tanpa teks terbaca), fakta hanya berisi
    pengirim + subject — tanpa pemisah " —" yang menggantung.
    """
    snippet = _truncate(_clean(body), SNIPPET_MAX_CHARS)
    fact = f"Dari {_clean(sender)}: {_clean(subject)}"
    if snippet:
        fact += f" — {snippet}"
    return fact


@_per_identity_lock
def sync_gmail(identity: str, connector: Any = None) -> dict[str, int]:
    """Tarik email 1 hari terakhir yang belum diproses → fakta memory.

    Mengembalikan ``{"added": N, "skipped": M}``; ``skipped`` = email yang
    dilewati karena noise (pengirim otomatis/newsletter, subject kosong)
    atau karena isinya gagal dibaca (tidak dicatat di watermark supaya bisa
    dicoba lagi di run berikutnya).
    """
    conn = _resolve_connector(connector, "google")
    result = conn.gmail_search("newer_than:1d", limit=20)
    if not result or not result.strip() or result.strip().lower().startswith("no messages"):
        return {"added": 0, "skipped": 0}

    store = MemoryStore(identity)
    seen = _read_seen(identity, "gmail")
    added = 0
    skipped = 0
    for line in result.splitlines():
        parsed = _parse_search_line(line)
        if parsed is None:
            # Baris sampah dari konektor: catat sebagai skipped agar
            # ringkasan akurat (tidak hilang diam-diam).
            skipped += 1
            continue
        message_id, _, _sender_search, _subject_search = parsed
        if message_id in seen:
            continue
        try:
            subject, sender, _date, body = _parse_read_message(conn.gmail_read(message_id))
        except Exception:
            # Jangan tandai seen: run berikutnya mencoba lagi.
            skipped += 1
            continue
        if _sender_is_noise(sender) or not _clean(subject):
            skipped += 1
            seen.add(message_id)
            continue
        fact = _gmail_fact(sender, subject, body)
        response = store.add(fact, source="gmail-sync", confidence=SYNC_CONFIDENCE)
        if response.startswith("OK"):
            added += 1
            seen.add(message_id)
        elif response == "That fact is already in memory.":
            # Sudah tersimpan (duplikat persis): tidak perlu dicoba lagi.
            seen.add(message_id)
        else:
            # ERROR (mis. memory penuh): jangan tandai seen — run berikutnya
            # mencoba lagi setelah ada ruang, bukan kehilangan fakta diam-diam.
            skipped += 1
            continue
    _write_seen(identity, "gmail", seen)
    return {"added": added, "skipped": skipped}


# --- Calendar -------------------------------------------------------------

def _parse_calendar_line(line: str) -> tuple[str, str] | None:
    """Pecah satu baris hasil calendar_list: ``when — summary``."""
    when, sep, title = line.partition(" — ")
    if not sep:
        return None
    when, title = _clean(when), _clean(title)
    if not title or title.lower() == "(no title)":
        return None
    return when, title


@_per_identity_lock
def sync_calendar(identity: str, connector: Any = None) -> dict[str, int]:
    """Acara 7 hari ke depan yang belum diproses → fakta memory.

    Watermark memakai hash baris ``when — title`` karena calendar_list tidak
    mengekspos event ID di output teksnya.
    """
    conn = _resolve_connector(connector, "google")
    now = time.time()
    time_min = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    time_max = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7 * 24 * 3600))
    result = conn.calendar_list(time_min, time_max, limit=20)
    if not result or not result.strip() or result.strip().lower().startswith("no upcoming"):
        return {"added": 0, "skipped": 0}

    store = MemoryStore(identity)
    seen = _read_seen(identity, "calendar")
    added = 0
    skipped = 0
    for line in result.splitlines():
        parsed = _parse_calendar_line(line)
        if parsed is None:
            skipped += 1
            continue
        when, title = parsed
        seen_key = hashlib.sha256(f"{when} — {title}".encode("utf-8")).hexdigest()
        if seen_key in seen:
            continue
        fact = f"Acara: {title} pada {when}" if when and when != "?" else f"Acara: {title}"
        response = store.add(fact, source="calendar-sync", confidence=SYNC_CONFIDENCE)
        if response.startswith("OK"):
            added += 1
            seen.add(seen_key)
        elif response == "That fact is already in memory.":
            seen.add(seen_key)
        else:
            # Memory penuh: jangan tandai seen, coba lagi run berikutnya.
            skipped += 1
    _write_seen(identity, "calendar", seen)
    return {"added": added, "skipped": skipped}


# --- GitHub ---------------------------------------------------------------

#: Hanya suffix pola ref PR ``(base→head)`` yang dibuang; judul issue yang
#: sah diakhiri teks dalam kurung (mis. "#12 Fix login (urgent)") TIDAK
#: boleh terpotong.
_GITHUB_LINE_RE = re.compile(r"^#(\d+)\s+(.+?)(?:\s+\([^\s()]+→[^\s()]+\))?$")


def _parse_github_line(line: str) -> tuple[str, str] | None:
    """Pecah baris list_issues/list_prs: ``#N judul`` (suffix ``(ref→ref)`` dibuang)."""
    match = _GITHUB_LINE_RE.match(_clean(line))
    if not match:
        return None
    number, title = match.group(1), _clean(match.group(2))
    return (number, title) if title else None


@_per_identity_lock
def sync_github(identity: str, connector: Any = None) -> dict[str, int]:
    """Issue/PR open terbaru di repo yang dikonfigurasi → fakta memory.

    Tanpa ``MEMORY_SYNC_GITHUB_REPOS`` fungsi ini no-op total: tidak ada
    pemanggilan konektor sama sekali (sync tidak boleh menebak repo mana
    yang peduli bagi operator).
    """
    repos = _github_repos()
    if not repos:
        return {"added": 0, "skipped": 0}
    conn = _resolve_connector(connector, "github")

    store = MemoryStore(identity)
    seen = _read_seen(identity, "github")
    added = 0
    skipped = 0
    for full in repos:
        owner, sep, repo = full.partition("/")
        if not sep or not owner.strip() or not repo.strip():
            skipped += 1
            continue
        owner, repo = owner.strip(), repo.strip()
        for kind, lister in (("issue", conn.list_issues), ("PR", conn.list_prs)):
            try:
                result = lister(owner, repo, state="open", limit=5)
            except Exception:
                skipped += 1
                continue
            if not result or not result.strip():
                continue
            for line in result.splitlines():
                parsed = _parse_github_line(line)
                if parsed is None:
                    skipped += 1
                    continue
                number, title = parsed
                seen_key = f"{owner}/{repo}#{kind}#{number}"
                if seen_key in seen:
                    continue
                fact = f"GitHub {owner}/{repo} {kind} #{number}: {_truncate(title, 200)}"
                response = store.add(fact, source="github-sync", confidence=SYNC_CONFIDENCE)
                if response.startswith("OK"):
                    added += 1
                    seen.add(seen_key)
                elif response == "That fact is already in memory.":
                    seen.add(seen_key)
                else:
                    # Memory penuh: jangan tandai seen, coba lagi run berikutnya.
                    skipped += 1
    _write_seen(identity, "github", seen)
    return {"added": added, "skipped": skipped}


# --- Entry point ----------------------------------------------------------

def _zero_summary() -> dict[str, int]:
    return {"added": 0, "skipped": 0}


def sync_all(identity: str) -> dict[str, Any]:
    """Jalankan semua sync untuk satu identity, dengan isolasi error per source.

    Bila ``MEMORY_SYNC_ENABLED`` false: no-op, tidak ada konektor yang
    disentuh, ringkasan menjelaskan kenapa. Bila satu source melempar
    exception, pesan error-nya dicatat di ``errors`` dan source lain tetap
    jalan.

    Bentuk kembalian::

        {"gmail": {"added": N, "skipped": M},
         "calendar": {"added": N, "skipped": M},
         "github": {"added": N, "skipped": M},
         "errors": {"gmail": "..."},
         "enabled": True}
        # Bila disabled: {"gmail": {...nol...}, ..., "enabled": False,
        #                "note": "memory sync disabled (...)"}
    """
    summary: dict[str, Any] = {
        "gmail": _zero_summary(),
        "calendar": _zero_summary(),
        "github": _zero_summary(),
        "errors": {},
        "enabled": _enabled(),
    }
    if not summary["enabled"]:
        summary["note"] = "memory sync disabled (MEMORY_SYNC_ENABLED is false)"
        return summary

    jobs = (("gmail", sync_gmail), ("calendar", sync_calendar), ("github", sync_github))
    for name, job in jobs:
        try:
            summary[name] = job(identity)
        except Exception as exc:
            summary[name] = _zero_summary()
            summary["errors"][name] = f"{type(exc).__name__}: {exc}"
    return summary
