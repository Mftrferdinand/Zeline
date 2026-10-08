r"""Telemetri pemakaian skill Zeline: fondasi self-improving skills.

Masalah yang dijawab modul ini sederhana: Zeline punya puluhan skill (bawaan,
private, dan hasil ``manage_skill``), tapi tidak ada catatan skill mana yang
sering dipakai, mana yang sering gagal, dan mana yang sudah tidak disentuh
berbulan-bulan. Tanpa catatan itu, keputusan "skill ini perlu diperbaiki" hanya
bisa ditebak — modul ini mengganti tebakan dengan angka.

Yang dicatat per skill per identitas: berapa kali dimuat, berapa outcome
sukses/gagal, total durasi pemakaian, kapan terakhir dipakai, kategori error
terakhir, dan berapa kali gagal beruntun. Dari angka mentah ini, curator atau
supervisor bisa menurunkan skor kesehatan skill tanpa pernah membaca isi
percakapan.

PRIVASI (aturan keras, ditegakkan modul ini):

* Yang disimpan HANYA metadata: nama skill, counter, durasi, dan KATEGORI
  error. Tidak pernah isi percakapan, argumen tool, atau teks error mentah.
* ``record_outcome`` menerima ``error_kind`` sebagai kategori, BUKAN teks
  error. Modul tetap menyaring ulang: hanya karakter ``[a-zA-Z0-9_:.\-#]``
  yang lolos (maks 60 karakter), sisanya dibuang — sehingga kalaupun pemanggil
  lalai mengirim teks mentah, teks itu tidak pernah mendarat utuh di disk.
  ``#`` diizinkan karena ia penanda hasil penyamaran digit (lihat
  ``_normalize_error_kind``): masking bersifat idempoten.
* Penyimpanan meniru ``zeline.tasks``: satu file JSON per identitas, nama file
  hash sha256(identity)[:32], direktori 0700, file 0600, tulis atomik lewat
  file-temp + replace, file rusak dibaca sebagai kosong tanpa raise.

``record_load`` dan ``record_outcome`` TIDAK PERNAH raise: seluruh badannya
dibungkus try/except. Telemetri adalah pengamat — ia tidak boleh merusak
pemanggilnya, bahkan ketika disk penuh atau direktori storage tidak bisa
ditulis.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from zeline import config

#: Karakter yang diizinkan dalam kategori error. Spasi, kutip, dan semua
#: karakter lain dibuang — inilah yang membuat teks percakapan tidak bisa
#: lolos utuh lewat ``error_kind``. ``#`` dikecualikan dari pembuangan karena
#: ia adalah penanda hasil penyamaran digit (``_DIGIT_RUN`` -> ``"#"``):
#: tanpanya, teks yang sudah di-masking berubah lagi saat dinormalisasi
#: ulang (mis. saat dibaca dari disk lewat ``_coerce_record``) — masking
#: tidak idempoten dan penanda samaran hilang dari data tersimpan.
_ERROR_KIND_CHARS = re.compile(r"[^a-zA-Z0-9_:.\-#]")

#: Rangkaian 4+ digit disamarkan — nomor telepon/NIK/angka sensitif lain
#: tidak bisa direkonstruksi dari file telemetri.
_DIGIT_RUN = re.compile(r"\d{4,}")

#: Suffix identitas worker yang dikupas ke identitas owner.
_WORKER_SUFFIX = re.compile(r"::(wkr|sub)[A-Za-z0-9]*$")

#: Batas defensif, semangatnya sama seperti tasks: satu chat publik tidak boleh
#: membuat satu identitas menggelembungkan disk owner.
MAX_SKILLS = 500
MAX_SKILL_NAME_CHARS = 128
MAX_ERROR_KIND_CHARS = 60

#: Kunci record yang ditulis ke disk. ``_read`` membuang kunci asing supaya
#: file yang diedit manual tidak bisa menyelipkan data tak dikenal.
_RECORD_KEYS = (
    "loads",
    "successes",
    "failures",
    "total_duration_s",
    "last_used_ts",
    "last_error_kind",
    "consecutive_failures",
)

#: Scope aktif per konteks eksekusi; ``None`` berarti di luar scope.
_active_scope: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar(
    "skill_telemetry_active", default=None
)

#: Serialisasi read-modify-write antar thread dalam satu proses.
_lock = threading.Lock()


def telemetry_dir() -> Path:
    """Direktori penyimpanan file telemetri per identitas."""
    return config.DATA_DIR / "skill-telemetry"


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def _path(identity: str) -> Path:
    return telemetry_dir() / f"{_key(identity)}.json"


def owner_identity(identity: str) -> str:
    """Kupas suffix worker dari identitas ke identitas owner.

    Supervisor menjalankan worker dengan identitas turunan seperti
    ``alice::wkr3f8a2b1c`` atau ``alice::subxyz``; telemetri worker tetap
    dicatat di identitas ``alice`` supaya angka pemakaian skill tidak
    terpecah-pecah per worker. Identitas polos dikembalikan apa adanya,
    identitas kosong menjadi ``"cli:local"`` (seperti ``zeline.tasks``).
    """
    if not identity:
        return "cli:local"
    owner = identity
    while True:
        stripped = _WORKER_SUFFIX.sub("", owner)
        if stripped == owner:
            break
        owner = stripped
    return owner or "cli:local"


def _normalize_skill_name(skill_name: Any) -> str | None:
    """Nama skill ternormalisasi, atau ``None`` bila invalid.

    Invalid (bukan string, kosong setelah strip) membuat pemanggil publik
    return diam-diam tanpa raise — telemetri tidak boleh memaksa pemanggil
    memvalidasi inputnya dulu.
    """
    if not isinstance(skill_name, str):
        return None
    name = skill_name.strip().lower()
    if not name:
        return None
    return name[:MAX_SKILL_NAME_CHARS]


def _normalize_error_kind(error_kind: Any) -> str:
    r"""Saring kategori error ke karakter aman saja.

    Hanya ``[a-zA-Z0-9_:.\-#]`` yang dipertahankan, maks 60 karakter, dan
    setiap rangkaian 4+ digit disamarkan menjadi ``"#"`` — nomor telepon,
    NIK, PIN, dan angka sensitif lain tidak bisa direkonstruksi dari disk.

    Idempoten: ``#`` adalah karakter yang diizinkan, jadi teks yang sudah
    di-masking tidak berubah saat dinormalisasi ulang (mis. saat record
    dibaca kembali dari disk) — penanda samaran tidak rusak/hilang.

    Batasan jujur: nama orang yang terkonkatenasi tanpa spasi (mis.
    ``"BudiSantoso"``) tetap lolos saringan karakter — saringan ini bukan
    pendeteksi PII. Karena itu pemanggil WAJIB mengirim kategori dari
    himpunan tertutup (``"verify_failed"``, ``"exception:TypeError"``),
    bukan kalimat bebas. Satu-satunya pemanggil saat ini (hook supervisor)
    sudah mematuhinya.
    """
    if not isinstance(error_kind, str):
        return ""
    cleaned = _ERROR_KIND_CHARS.sub("", error_kind)[:MAX_ERROR_KIND_CHARS]
    return _DIGIT_RUN.sub("#", cleaned)


def _default_record() -> dict[str, Any]:
    return {
        "loads": 0,
        "successes": 0,
        "failures": 0,
        "total_duration_s": 0.0,
        "last_used_ts": 0.0,
        "last_error_kind": "",
        "consecutive_failures": 0,
    }


def _coerce_record(raw: Any) -> dict[str, Any] | None:
    """Validasi satu record dari disk; ``None`` bila bentuknya tidak sah."""
    if not isinstance(raw, dict):
        return None
    record = _default_record()
    try:
        record["loads"] = int(raw.get("loads") or 0)
        record["successes"] = int(raw.get("successes") or 0)
        record["failures"] = int(raw.get("failures") or 0)
        record["total_duration_s"] = float(raw.get("total_duration_s") or 0.0)
        record["last_used_ts"] = float(raw.get("last_used_ts") or 0.0)
        record["consecutive_failures"] = int(raw.get("consecutive_failures") or 0)
        kind = raw.get("last_error_kind") or ""
        record["last_error_kind"] = (
            _normalize_error_kind(kind) if isinstance(kind, str) else ""
        )
    except (TypeError, ValueError):
        return None
    if any(record[key] < 0 for key in ("loads", "successes", "failures",
                                      "total_duration_s", "last_used_ts",
                                      "consecutive_failures")):
        return None
    return record


def _read(identity: str) -> dict[str, dict[str, Any]]:
    """Seluruh record telemetri satu identitas. File rusak → kosong, tanpa raise."""
    try:
        raw = json.loads(_path(identity).read_text(encoding="utf-8"))
    except Exception:
        # Sengaja luas: file rusak dalam bentuk apapun (bukan JSON, byte
        # UTF-8 invalid, I/O error) dibaca sebagai kosong, tanpa raise.
        return {}
    if not isinstance(raw, dict):
        return {}
    records: dict[str, dict[str, Any]] = {}
    for name, entry in raw.items():
        clean_name = _normalize_skill_name(name)
        if clean_name is None:
            continue
        record = _coerce_record(entry)
        if record is not None:
            records[clean_name] = record
    return records


def _write(identity: str, records: dict[str, dict[str, Any]]) -> None:
    """Tulis atomik ala tasks.py: temp unik per pid + chmod 0600 + replace."""
    directory = telemetry_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = _path(identity)
    temporary = target.with_name(f"{target.stem}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)


def _record(records: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    """Ambil record skill, buat default bila belum ada (bounded)."""
    record = records.get(name)
    if record is None:
        if len(records) >= MAX_SKILLS:
            # Penuh: jangan tolak diam-diam, tapi jangan tumbuh tanpa batas —
            # timpa record yang paling lama tidak dipakai.
            oldest = min(records, key=lambda key: records[key]["last_used_ts"])
            del records[oldest]
        record = _default_record()
        records[name] = record
    return record


def record_load(skill_name: Any, identity: str) -> None:
    """Catat satu pemuatan skill. TIDAK PERNAH raise.

    Dipanggil setiap kali sebuah skill dimuat/dipakai, sebelum atau sesudah
    eksekusi. ``loads`` +1 dan ``last_used_ts`` diperbarui.
    """
    try:
        name = _normalize_skill_name(skill_name)
        if name is None:
            return
        owner = owner_identity(identity)
        with _lock:
            records = _read(owner)
            record = _record(records, name)
            record["loads"] += 1
            record["last_used_ts"] = time.time()
            _write(owner, records)
    except Exception:
        # Telemetri tidak boleh merusak pemanggilnya — kegagalan apapun
        # (disk penuh, direktori tak bisa ditulis, bug internal) ditelan.
        pass


def record_outcome(
    skill_name: Any,
    identity: str,
    ok: bool,
    duration_s: float = 0.0,
    error_kind: str = "",
) -> None:
    """Catat hasil eksekusi sebuah skill. TIDAK PERNAH raise.

    ``ok=True``: ``successes`` +1, ``consecutive_failures`` direset ke 0.
    ``ok=False``: ``failures`` +1, ``consecutive_failures`` +1, dan
    ``last_error_kind`` diisi kategori error yang sudah disaring (lihat
    PRIVASI di docstring modul). ``duration_s`` negatif diperlakukan sebagai 0.
    """
    try:
        name = _normalize_skill_name(skill_name)
        if name is None:
            return
        owner = owner_identity(identity)
        with _lock:
            records = _read(owner)
            record = _record(records, name)
            now = time.time()
            record["last_used_ts"] = now
            try:
                duration = float(duration_s or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            record["total_duration_s"] += max(0.0, duration)
            if ok:
                record["successes"] += 1
                record["consecutive_failures"] = 0
            else:
                record["failures"] += 1
                record["consecutive_failures"] += 1
                record["last_error_kind"] = _normalize_error_kind(error_kind)
            _write(owner, records)
    except Exception:
        pass


def _with_rate(record: dict[str, Any]) -> dict[str, Any]:
    """Salinan record + ``success_rate`` (``None`` bila belum ada outcome)."""
    view = dict(record)
    decided = record["successes"] + record["failures"]
    view["success_rate"] = (
        record["successes"] / decided if decided else None
    )
    return view


def stats(skill_name: Any, identity: str) -> dict[str, Any]:
    """Statistik satu skill + ``success_rate``; kosong bila tak dikenal/invalid."""
    name = _normalize_skill_name(skill_name)
    if name is None:
        return {}
    record = _read(owner_identity(identity)).get(name)
    if record is None:
        return {}
    return _with_rate(record)


def global_stats(skill_name: Any) -> dict[str, Any]:
    """Agregat statistik satu skill di SEMUA identitas.

    Direktori skill dipakai bersama antar identitas, jadi keputusan yang
    menyentuh direktori bersama (arsip) harus melihat pemakaian global,
    bukan hanya satu identitas. File yang rusak/diedit manual dilewati
    diam-diam (fail-safe, seperti ``_read``).

    ``consecutive_failures`` global adalah MAKSIMUM antar identitas, bukan
    jumlah — ia mengukur streak dalam satu konteks pemakaian, dan
    penjumlahan streak antar konteks tidak bermakna.

    Mengembalikan ``{}`` bila skill tidak dikenal di identitas mana pun
    (konsisten dengan ``stats()``).
    """
    name = _normalize_skill_name(skill_name)
    if name is None:
        return {}
    total = _default_record()
    max_cons_fail = 0
    found = False
    try:
        files = sorted(telemetry_dir().glob("*.json"))
    except OSError:
        return {}
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record = _coerce_record(raw.get(name)) if isinstance(raw, dict) else None
        if record is None:
            continue
        found = True
        total["loads"] += record["loads"]
        total["successes"] += record["successes"]
        total["failures"] += record["failures"]
        total["total_duration_s"] += record["total_duration_s"]
        max_cons_fail = max(max_cons_fail, record["consecutive_failures"])
    if not found:
        return {}
    total["consecutive_failures"] = max_cons_fail
    return _with_rate(total)


def all_stats(identity: str) -> dict[str, dict[str, Any]]:
    """Statistik seluruh skill satu identitas, masing-masing + ``success_rate``."""
    return {
        name: _with_rate(record)
        for name, record in _read(owner_identity(identity)).items()
    }


@contextlib.contextmanager
def usage_scope() -> Iterator[None]:
    """Kumpulkan nama skill yang dipakai dalam satu scope eksekusi.

    Mekanisme atribusi untuk supervisor: bungkus eksekusi satu worker dengan
    scope ini, panggil ``note_used`` setiap kali skill dipakai, lalu baca
    ``skills_in_scope`` untuk tahu skill apa saja yang tersentuh. Berbasis
    ``ContextVar`` — tiap thread punya konteks sendiri sehingga dua worker
    paralel tidak bocor silang. Scope boleh nested; scope dalam tidak
    mengganggu scope luar.
    """
    token = _active_scope.set(set())
    try:
        yield
    finally:
        _active_scope.reset(token)


def note_used(skill_name: Any) -> None:
    """Catat pemakaian skill ke scope aktif; no-op bila di luar scope."""
    try:
        current = _active_scope.get()
        if current is None:
            return
        name = _normalize_skill_name(skill_name)
        if name is not None:
            current.add(name)
    except Exception:
        pass


def skills_in_scope() -> frozenset[str]:
    """Nama skill yang dicatat ``note_used`` dalam scope aktif (kosong bila tak ada)."""
    try:
        current = _active_scope.get()
    except Exception:
        return frozenset()
    return frozenset(current) if current is not None else frozenset()
