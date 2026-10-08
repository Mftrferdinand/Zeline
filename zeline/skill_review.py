"""Mesin review periodik skill Zeline (self-improving skills).

Modul ini memperluas :mod:`zeline.curator`: bukan cuma memindai dan
mengarsipkan skill basi, tapi juga menilai *kualitas* pemakaian tiap skill
berdasarkan telemetri (:mod:`zeline.skill_telemetry`) lalu menyusun rencana
perubahan:

- ``promote``   — skill yang terbukti berguna dinaikkan prioritas (+1) supaya
  muncul lebih dulu di urutan pemuatan.
- ``demote``    — skill yang sering gagal diturunkan (-1); tetap bisa dipakai,
  hanya diurut terakhir. Ini BUKAN penghapusan.
- ``archive``   — skill yang tidak pernah dipakai (nol ``loads`` DAN nol
  ``outcome`` di SEMUA identitas — bukan cuma identitas ini) dan basi, atau
  yang gagal beruntun, dipindah ke ``.archive/`` lewat ``curator.archive()``
  (selalu bisa di-restore; tidak ada hapus permanen di modul ini). Pengaman:
  arsip "tidak pernah dipakai" hanya jalan kalau telemetri sudah punya data
  untuk identity tersebut — kalau telemetri masih kosong (baru dipasang),
  ``loads==0`` tidak dianggap bukti dan skill lama tidak diarsip massal.
- ``report_overlap`` — duplikat deskripsi dari ``curator.scan()`` hanya
  dilaporkan; tidak ada aksi otomatis.

Prinsip sakral yang diwarisi dari curator: **skill bawaan/public tidak boleh
disentuh**. Rencana ``promote``/``demote``/``archive`` hanya dibuat untuk
skill private milik user. Kalau ``skills_dir`` yang dipindai adalah direktori
public, review hanya menghasilkan laporan overlap.

Semua mutasi dicatat di change log per-identity (JSONL) lewat
``curator.log_action()`` dengan format
``{id, ts, action, skill, reason, previous_state}`` sehingga tiap perubahan
bisa di-rollback lewat :func:`rollback_change`.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from zeline import config as _config
from zeline import curator as _curator
from zeline import skills as _skills

#: Level prioritas skill.
PRIORITY_PROMOTED = 1
PRIORITY_NORMAL = 0
PRIORITY_DEMOTED = -1
_VALID_LEVELS = (PRIORITY_DEMOTED, PRIORITY_NORMAL, PRIORITY_PROMOTED)

# ---------------------------------------------------------------------------
# Ambang heuristik review.
#
# Angka-angka di bawah sengaja konservatif: review berjalan periodik dan
# otomatis, jadi lebih baik *melewatkan* skill yang sebenarnya layak
# dipromote/demote daripada salah menilai skill yang datanya masih sedikit.
# Semua ambang bersifat inklusif/eksklusif sesuai namanya di docstring tiap
# pemakaian.
# ---------------------------------------------------------------------------

#: Minimal total pemuatan sebelum skill layak dipromote. Alasan: skill yang
#: baru dipakai 1-2 kali belum punya cukup sinyal; 5x pemakaian adalah sinyal
#: awal yang wajar tanpa menunggu terlalu lama.
PROMOTE_MIN_LOADS = 5
#: Minimal outcome (sukses+gagal) sebelum success_rate dipercaya untuk
#: promote. Alasan: tanpa ini, skill dengan 1 sukses dari 1 outcome
#: (rate 1.0) langsung dipromote — terlalu sedikit data.
PROMOTE_MIN_OUTCOMES = 3
#: Ambang success_rate untuk promote. Alasan: >= 0.8 berarti skill jelas-jelas
#: membantu; di bawah itu masih "biasa saja" dan belum layak prioritas +1.
PROMOTE_MIN_SUCCESS_RATE = 0.8

#: Minimal outcome sebelum skill boleh didemote. Alasan: sama seperti promote,
#: jangan menghukum skill yang datanya masih sedikit (mis. 1 gagal dari
#: 1 outcome bukan bukti skill jelek).
DEMOTE_MIN_OUTCOMES = 3
#: Skill didemote kalau success_rate di BAWAH angka ini. Alasan: < 0.4 berarti
#: lebih sering gagal daripada berhasil — cukup buruk untuk diurut terakhir,
#: tapi tidak cukup buruk untuk diarsipkan (masih bisa dipakai manual).
DEMOTE_MAX_SUCCESS_RATE = 0.4

#: Gagal beruntun minimal untuk pengarsipan. Alasan: 5x gagal berturut-turut
#: adalah pola kerusakan yang jelas, bukan nasib buruk sesekali.
ARCHIVE_MIN_CONSECUTIVE_FAILURES = 5
#: Minimal total outcome untuk pengarsipan karena gagal beruntun. Alasan:
#: consecutive_failures=5 tanpa minimal outcome bisa terpicu oleh 5 outcome
#: yang semuanya memang gagal — itu justru kasusnya, jadi syarat ini
#: memastikan angka consecutive_failures didukung data outcome yang cukup
#: (mencegah arsip dari statistik setengah jalan).
ARCHIVE_MIN_OUTCOMES = 5

#: Ambang success_rate GLOBAL untuk koroborasi arsip karena gagal beruntun.
#: Alasan: direktori skill dipakai bersama antar identitas — arsip karena
#: gagal butuh bukti bahwa skill memang rusak, bukan cuma gagal di konteks
#: satu identitas. Kalau identitas lain memakainya dengan sukses (global
#: rate >= 0.5 dari data yang cukup), kegagalan itu spesifik konteks dan
#: demote per-identitas lebih tepat daripada mengarsip untuk semua orang.
ARCHIVE_GLOBAL_MAX_SUCCESS_RATE = 0.5

#: Batas umur default untuk pengarsipan skill yang tidak pernah dipakai.
#: Disamakan dengan curator.DEFAULT_STALE_DAYS supaya definisi "basi"
#: konsisten di seluruh codebase.
DEFAULT_STALE_DAYS = _curator.DEFAULT_STALE_DAYS

#: Nama direktori arsip di bawah DATA_DIR untuk change log review.
REVIEW_LEDGER_DIR_NAME = "skill-review"
#: Nama direktori store prioritas di bawah DATA_DIR.
PRIORITY_DIR_NAME = "skill-priority"


# ---------------------------------------------------------------------------
# Utilitas path
# ---------------------------------------------------------------------------

def _identity_hash(identity: str) -> str:
    """32 karakter pertama sha256(identity); dipakai sebagai nama file."""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _priority_dir(priority_dir: Path | str | None) -> Path:
    if priority_dir:
        return Path(priority_dir).expanduser()
    return _config.DATA_DIR / PRIORITY_DIR_NAME


def _priority_path(identity: str, priority_dir: Path | str | None = None) -> Path:
    # Prioritas dinormalisasi ke identitas OWNER (lihat _owner_identity):
    # prioritas per-identitas tidak boleh bocor/terfragmentasi antar
    # identitas turunan (worker) milik owner yang sama.
    return _priority_dir(priority_dir) / f"{_identity_hash(_owner_identity(identity))}.json"


def _owner_identity(identity: str) -> str:
    """Normalisasi identitas ke identitas owner (kupas suffix worker).

    Supervisor menjalankan worker dengan identitas turunan seperti
    ``alice::wkr3f8a2b1c``; priority store dipakai bersama antara owner dan
    worker-nya. Tanpa normalisasi ini, ``alice`` dan ``alice::wkr3f8a``
    membaca/menulis file prioritas yang BERBEDA — review terfragmentasi per
    worker dan prioritas "bocor" (tidak konsisten) antar identitas turunan.

    Resolusi malas via ``importlib`` — pola yang sama seperti
    ``review_skills`` — supaya fake ``zeline.skill_telemetry`` di test tetap
    terpakai. Bila telemetri tak tersedia, fallback ke identitas mentah
    (perilaku lama).
    """
    try:
        telemetry = importlib.import_module("zeline.skill_telemetry")
        return telemetry.owner_identity(identity)
    except Exception:
        return identity


def _review_ledger_path(
    identity: str, ledger_path: Path | str | None = None
) -> Path:
    if ledger_path:
        return Path(ledger_path).expanduser()
    # Ledger dinormalisasi ke identitas OWNER seperti priority store (lihat
    # _priority_path): review via identitas worker (``alice::wkr3f8a``)
    # harus menulis ke file yang SAMA dengan owner-nya, bukan file mati
    # per-worker yang tidak pernah dibaca lagi.
    return (
        _config.DATA_DIR
        / REVIEW_LEDGER_DIR_NAME
        / f"{_identity_hash(_owner_identity(identity))}.jsonl"
    )


def _read_priority_map(identity: str, priority_dir: Path | str | None) -> dict:
    path = _priority_path(identity, priority_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Bersihkan isi korup: hanya level valid yang dipakai.
    return {
        name: level
        for name, level in data.items()
        if isinstance(level, int) and level in _VALID_LEVELS
    }


def _write_priority_map(
    identity: str, mapping: dict, priority_dir: Path | str | None
) -> None:
    directory = _priority_dir(priority_dir)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = _priority_path(identity, priority_dir)
    # Tulis atomik: file sementara + replace, pola yang sama dengan tasks.py.
    # Nama sementara unik per proses supaya dua penulis bersamaan tidak
    # saling menimpa (pelajaran dari bug scheduler jobs.json).
    temporary = target.with_name(f"{target.stem}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _scope_of(skills_dir: Path) -> str:
    """Kembalikan 'private', 'public', atau 'unknown'.

    'unknown' dipakai untuk direktori fixture di test (bukan direktori
    skill bawaan) — diperlakukan sama seperti private supaya heuristiknya
    bisa diuji tanpa menyentuh ~/.zeline asli.
    """
    resolved = skills_dir.resolve()
    try:
        public = Path(_skills.PUBLIC_SKILLS_DIR).resolve()
        private = Path(_skills.PRIVATE_SKILLS_DIR).resolve()
    except OSError:
        return "unknown"
    if resolved == public or public in resolved.parents:
        return "public"
    if resolved == private or private in resolved.parents:
        return "private"
    return "unknown"


def _stats_numbers(stats: dict | None) -> dict:
    """Normalisasi dict statistik telemetri ke angka yang dibutuhkan review."""
    stats = stats or {}
    loads = int(stats.get("loads", 0) or 0)
    successes = int(stats.get("successes", 0) or 0)
    failures = int(stats.get("failures", 0) or 0)
    consecutive_failures = int(stats.get("consecutive_failures", 0) or 0)
    outcomes = successes + failures
    rate = stats.get("success_rate")
    if rate is None:
        rate = (successes / outcomes) if outcomes else 0.0
    return {
        "loads": loads,
        "successes": successes,
        "failures": failures,
        "outcomes": outcomes,
        "consecutive_failures": consecutive_failures,
        "success_rate": float(rate),
    }


# ---------------------------------------------------------------------------
# Priority store
# ---------------------------------------------------------------------------

def get_priority(
    skill_name: str,
    identity: str,
    priority_dir: Path | str | None = None,
) -> int:
    """Kembalikan level prioritas skill; default 0 (normal) bila belum diset."""
    mapping = _read_priority_map(identity, priority_dir)
    level = mapping.get(skill_name, PRIORITY_NORMAL)
    return level if level in _VALID_LEVELS else PRIORITY_NORMAL


def set_priority(
    skill_name: str,
    identity: str,
    level: int,
    reason: str,
    priority_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> dict:
    """Set level prioritas skill (-1/0/+1) dan catat perubahannya.

    Mengembalikan change record ``{id, ts, action, skill, previous_level,
    new_level, reason}``; record yang sama juga ditulis ke change log lewat
    ``curator.log_action()`` supaya bisa di-rollback.
    """
    _curator._check_name(skill_name)
    if level not in _VALID_LEVELS:
        raise ValueError(
            f"Level prioritas tidak valid: {level!r} "
            f"(harus salah satu dari {_VALID_LEVELS})"
        )
    mapping = _read_priority_map(identity, priority_dir)
    previous = mapping.get(skill_name, PRIORITY_NORMAL)
    mapping[skill_name] = level
    _write_priority_map(identity, mapping, priority_dir)
    record = _curator.log_action(
        "set_priority",
        skill_name,
        {
            "reason": reason,
            "previous_state": {
                "previous_level": previous,
                "new_level": level,
            },
        },
        ledger_path=_review_ledger_path(identity, ledger_path),
    )
    return {
        "id": record["id"],
        "ts": record["ts"],
        "action": "set_priority",
        "skill": skill_name,
        "previous_level": previous,
        "new_level": level,
        "reason": reason,
    }


def ranked_order(
    names: list[str],
    identity: str,
    priority_dir: Path | str | None = None,
) -> list[str]:
    """Urutkan nama skill: +1 dulu, 0 di tengah, -1 terakhir (stabil).

    Stabil artinya skill dengan level sama mempertahankan urutan asal —
    review tidak boleh mengacak urutan yang sudah ditentukan user.
    """
    mapping = _read_priority_map(identity, priority_dir)
    indexed = [
        (-mapping.get(name, PRIORITY_NORMAL), index, name)
        for index, name in enumerate(names)
    ]
    indexed.sort(key=lambda item: (item[0], item[1]))
    return [name for _, _, name in indexed]


# ---------------------------------------------------------------------------
# Mesin review
# ---------------------------------------------------------------------------

def _plan_action(
    info: dict,
    numbers: dict,
    global_numbers: dict,
    priority: int,
    scope: str,
    stale_days: int,
    has_telemetry: bool,
) -> dict | None:
    """Tentukan satu aksi review untuk sebuah skill, atau None.

    ``numbers`` adalah statistik per-identitas (untuk promote/demote yang
    memang per-identitas); ``global_numbers`` adalah agregat semua identitas
    (untuk arsip yang menyentuh direktori skill bersama).
    """
    name = info["name"]
    loads = numbers["loads"]
    outcomes = numbers["outcomes"]
    rate = numbers["success_rate"]
    cons_fail = numbers["consecutive_failures"]
    g_loads = global_numbers["loads"]
    g_outcomes = global_numbers["outcomes"]
    g_rate = global_numbers["success_rate"]
    details = {
        "loads": loads,
        "successes": numbers["successes"],
        "failures": numbers["failures"],
        "success_rate": round(rate, 3),
        "consecutive_failures": cons_fail,
        "global_loads": g_loads,
        "global_outcomes": g_outcomes,
        "global_success_rate": round(g_rate, 3),
        "age_days": info.get("age_days"),
        "previous_level": priority,
    }
    if scope == "public":
        # Prinsip sakral: skill public tidak boleh disentuh — tidak ada
        # rencana mutasi apa pun untuknya.
        return None
    # Urutan cek: yang paling merusak (archive) dicek paling dulu supaya
    # tidak tertutup oleh promote/demote.
    #
    # Arsip karena gagal beruntun butuh koroborasi global: direktori skill
    # dipakai bersama, jadi review identitas A tidak boleh mengarsipkan
    # skill yang identitas B pakai dengan sukses.
    if (
        cons_fail >= ARCHIVE_MIN_CONSECUTIVE_FAILURES
        and outcomes >= ARCHIVE_MIN_OUTCOMES
        and (
            g_outcomes < ARCHIVE_MIN_OUTCOMES
            or g_rate < ARCHIVE_GLOBAL_MAX_SUCCESS_RATE
        )
    ):
        return {
            "skill": name,
            "action": "archive",
            "reason": (
                f"{cons_fail}x gagal beruntun dari {outcomes} outcome — "
                "pola kerusakan, bukan nasib buruk sesekali"
            ),
            "details": details,
        }
    # Arsip karena tak dipakai memakai angka GLOBAL: "tidak pernah dipakai"
    # per-identitas tidak cukup untuk menyentuh direktori bersama. Syaratnya
    # diketatkan menjadi NOL pemuatan DAN NOL outcome global — pola "bukti
    # global" yang sama seperti koroborasi arsip-gagal di atas: satu outcome
    # tercatat (mis. load gagal ditulis tapi outcome tercatat) berarti skill
    # pernah disentuh dan tidak boleh diarsip sebagai "tak pernah dipakai".
    if (
        has_telemetry
        and g_loads == 0
        and g_outcomes == 0
        and info.get("stale")
        and info.get("age_days", 0) > stale_days
    ):
        return {
            "skill": name,
            "action": "archive",
            "reason": (
                f"tidak pernah dipakai (nol pemuatan dan nol outcome di "
                f"semua identitas) dan basi "
                f"({info.get('age_days')} hari > {stale_days} hari)"
            ),
            "details": details,
        }
    if (
        loads >= PROMOTE_MIN_LOADS
        and outcomes >= PROMOTE_MIN_OUTCOMES
        and rate >= PROMOTE_MIN_SUCCESS_RATE
        and priority < PRIORITY_PROMOTED
    ):
        return {
            "skill": name,
            "action": "promote",
            "reason": (
                f"{loads}x dipakai, success_rate {rate:.0%} "
                f"dari {outcomes} outcome — terbukti berguna"
            ),
            "details": details,
        }
    if (
        outcomes >= DEMOTE_MIN_OUTCOMES
        and rate < DEMOTE_MAX_SUCCESS_RATE
        and priority > PRIORITY_DEMOTED
    ):
        return {
            "skill": name,
            "action": "demote",
            "reason": (
                f"success_rate {rate:.0%} dari {outcomes} outcome "
                "(< 40%) — lebih sering gagal; diurut terakhir, tetap bisa dipakai"
            ),
            "details": details,
        }
    return None


def review_skills(
    identity: str,
    apply: bool = False,
    skills_dir: Path | str | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    ledger_path: Path | str | None = None,
    curator_ledger_path: Path | str | None = None,
    priority_dir: Path | str | None = None,
) -> list[dict]:
    """Susun rencana review untuk semua skill, opsional langsung diterapkan.

    Tiap item rencana: ``{"skill", "action", "reason", "details"}`` dengan
    action salah satu dari ``promote``/``demote``/``archive``/``report_overlap``.

    Dengan ``apply=False`` (default) ini dry-run MURNI: tidak ada file yang
    ditulis — tidak di skills_dir, tidak di priority store, tidak di ledger
    mana pun. Dengan ``apply=True`` tiap aksi dijalankan: promote/demote via
    :func:`set_priority`, archive via ``curator.archive()`` (recoverable,
    bisa di-restore), dan setiap mutasi dicatat lewat ``curator.log_action()``.
    """
    # Import malas via importlib: modul review tetap bisa dipakai untuk fungsi
    # prioritas saja tanpa modul telemetri terpasang, dan import_module
    # membaca sys.modules secara langsung (tidak terpengaruh status atribut
    # paket) sehingga fake di test terpakai dengan benar.
    try:
        _telemetry = importlib.import_module("zeline.skill_telemetry")
    except ImportError as exc:
        raise _curator.CuratorError(
            "zeline.skill_telemetry belum tersedia; review butuh kontrak "
            "telemetri (record_load/record_outcome/stats/all_stats/"
            "owner_identity) untuk menilai skill."
        ) from exc

    root = _curator._resolve_skills_dir(skills_dir)
    scope = _scope_of(root)
    # Pengaman arsip "tidak pernah dipakai": kalau telemetri belum punya data
    # SAMA SEKALI untuk identity ini (mis. modul telemetri baru dipasang),
    # loads==0 tidak bisa dibedakan dari "belum tercatat" — jadi aturan
    # arsip unused+stale dimatikan supaya review pertama tidak mengarsip
    # massal skill lama yang sebenarnya masih dipakai. Aturan arsip karena
    # gagal beruntun tidak terpengaruh (ia punya datanya sendiri).
    has_telemetry = bool(_telemetry.all_stats(identity) or {})
    plan: list[dict] = []

    for info in _curator.scan(root, stale_days=stale_days):
        name = info["name"]
        numbers = _stats_numbers(_telemetry.stats(name, identity))
        # Angka global untuk keputusan arsip (direktori skill bersama).
        gnumbers = _stats_numbers(_telemetry.global_stats(name))
        priority = get_priority(name, identity, priority_dir)
        item = _plan_action(
            info, numbers, gnumbers, priority, scope, stale_days, has_telemetry
        )
        if item is not None:
            plan.append(item)
        duplicates = info.get("possible_duplicates") or []
        if duplicates:
            plan.append(
                {
                    "skill": name,
                    "action": "report_overlap",
                    "reason": (
                        "deskripsi mirip dengan skill lain "
                        f"({', '.join(duplicates)}); perlu ditinjau manual"
                    ),
                    "details": {
                        "duplicates": duplicates,
                        "previous_level": priority,
                    },
                }
            )

    if not apply:
        return plan

    return apply_plan(
        identity,
        plan,
        skills_dir=skills_dir,
        ledger_path=ledger_path,
        curator_ledger_path=curator_ledger_path,
        priority_dir=priority_dir,
    )


#: Aksi rencana yang dikenal ``apply_plan``. Validasi ringan anti-TOCTOU:
#: rencana yang dieksekusi harus berbentuk hasil ``review_skills``.
_KNOWN_PLAN_ACTIONS = frozenset(
    {"promote", "demote", "archive", "report_overlap"}
)


def _drop_cached_review_plan(identity: str) -> None:
    """Hapus rencana review yang di-cache di tool layer pasca-apply sukses.

    Cache-nya (``_REVIEW_PLAN_CACHE``) tinggal di ``zeline.tools`` — modul
    yang tidak boleh diimpor di top-level sini (lapisan tool mengimpor modul
    ini, bukan sebaliknya; import top-level akan melingkar). Dibersihkan
    lewat import malas yang dijaga: bila ``zeline.tools`` belum/tidak
    tersedia, tidak ada yang dibersihkan dan apply tetap dianggap sukses
    (cache punya TTL sendiri sebagai pengaman kedua).
    """
    try:
        tools = importlib.import_module("zeline.tools")
        cache = getattr(tools, "_REVIEW_PLAN_CACHE", None)
        if isinstance(cache, dict):
            cache.pop(identity, None)
    except Exception:
        pass


def apply_plan(
    identity: str,
    plan: list[dict],
    skills_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
    curator_ledger_path: Path | str | None = None,
    priority_dir: Path | str | None = None,
) -> list[dict]:
    """Terapkan rencana review yang SUDAH DISETUJUI operator.

    Ini satu-satunya jalur eksekusi rencana: ``review_skills(apply=True)``
    menghitung lalu mendelegasikan ke sini, dan tool ``apply_skill_review``
    meneruskan rencana persis yang tampil di pertanyaan approval. Tidak
    ada jalur yang menghitung ulang rencana diam-diam antara approval dan
    eksekusi — yang disetujui operator = yang dijalankan (anti-TOCTOU).

    Atomik lewat dua lapis:

    1. **Validasi di muka** — seluruh rencana divalidasi SEBELUM satu pun
       item dieksekusi; satu item asing/invalid membatalkan semuanya.
    2. **Kompensasi saat gagal** — bila satu item gagal di tengah eksekusi,
       aksi yang sudah berjalan dibatalkan satu per satu (urutan terbalik:
       prioritas dikembalikan ke level sebelumnya, arsip di-restore), flag
       ``applied`` item-item itu direset, lalu error asli di-raise ulang.
       Efek bersihnya: tidak ada apply setengah jalan.

    Item yang sudah bertanda ``applied`` dilewati sehingga pemanggilan
    ulang dengan rencana yang sama idempoten dan tidak meledak (mis.
    arsip ganda). Mengembalikan rencana yang sama dengan flag
    ``applied`` per item.

    Pasca-apply yang SUKSES PENUH, plan cache di tool layer
    (``zeline.tools._REVIEW_PLAN_CACHE``) dibersihkan supaya rencana yang
    baru dieksekusi tidak bisa di-apply ulang diam-diam.
    """
    root = _curator._resolve_skills_dir(skills_dir)
    review_ledger = _review_ledger_path(identity, ledger_path)
    # Validasi seluruh rencana DULU sebelum ada yang dieksekusi: satu item
    # asing membatalkan semuanya, bukan menyisakan apply setengah jalan.
    for item in plan:
        action = item.get("action")
        if action not in _KNOWN_PLAN_ACTIONS:
            raise _curator.CuratorError(
                f"Aksi rencana tidak dikenal: {action!r} "
                f"(skill {item.get('skill')!r}) — rencana ditolak utuh."
            )
        if not item.get("skill") or "reason" not in item:
            raise _curator.CuratorError(
                f"Item rencana tidak lengkap: {item!r} — rencana ditolak utuh."
            )
    # Lapis 2: eksekusi dengan kompensasi. Tiap aksi yang berhasil
    # mencatatkan cara membatalkannya; bila satu item gagal, semuanya
    # dibatalkan (terbalik) sebelum error di-raise ulang.
    compensations: list[tuple[dict, Callable[[], None]]] = []
    failed_action: str | None = None
    failed_skill: str | None = None
    try:
        for item in plan:
            if item.get("applied"):
                continue
            action = item["action"]
            name = item["skill"]
            failed_action, failed_skill = action, name
            if action == "promote":
                previous = get_priority(name, identity, priority_dir)
                set_priority(
                    name,
                    identity,
                    PRIORITY_PROMOTED,
                    reason=item["reason"],
                    priority_dir=priority_dir,
                    ledger_path=review_ledger,
                )
                compensations.append(
                    (
                        item,
                        lambda _n=name, _p=previous: set_priority(
                            _n,
                            identity,
                            _p,
                            reason="kompensasi: apply_plan gagal di tengah jalan",
                            priority_dir=priority_dir,
                            ledger_path=review_ledger,
                        ),
                    )
                )
            elif action == "demote":
                previous = get_priority(name, identity, priority_dir)
                set_priority(
                    name,
                    identity,
                    PRIORITY_DEMOTED,
                    reason=item["reason"],
                    priority_dir=priority_dir,
                    ledger_path=review_ledger,
                )
                compensations.append(
                    (
                        item,
                        lambda _n=name, _p=previous: set_priority(
                            _n,
                            identity,
                            _p,
                            reason="kompensasi: apply_plan gagal di tengah jalan",
                            priority_dir=priority_dir,
                            ledger_path=review_ledger,
                        ),
                    )
                )
            elif action == "archive":
                dst = _curator.archive(
                    name, skills_dir=root, ledger_path=curator_ledger_path
                )
                # Kompensasi dicatat SEBELUM log_action: bila pencatatan
                # gagal setelah arsip berhasil, restore tetap berjalan.
                compensations.append(
                    (
                        item,
                        lambda _n=name: _curator.restore(
                            _n,
                            skills_dir=root,
                            ledger_path=curator_ledger_path,
                        ),
                    )
                )
                _curator.log_action(
                    "archive",
                    name,
                    {
                        "reason": item["reason"],
                        "previous_state": {"archived_to": str(dst)},
                    },
                    ledger_path=review_ledger,
                )
            # report_overlap: laporan saja, tidak ada aksi otomatis.
            item["applied"] = action != "report_overlap"
    except Exception as exc:
        undo_errors: list[str] = []
        for done_item, undo in reversed(compensations):
            try:
                undo()
            except Exception as undo_exc:  # noqa: BLE001 — best-effort
                undo_errors.append(
                    f"{done_item.get('skill')!r}: {type(undo_exc).__name__}: {undo_exc}"
                )
            done_item["applied"] = False
        detail = (
            f"apply_plan gagal pada aksi {failed_action!r} untuk skill "
            f"{failed_skill!r} ({type(exc).__name__}: {exc}); "
            f"{len(compensations)} aksi yang sudah berjalan dibatalkan kembali."
        )
        if undo_errors:
            detail += (
                " PERHATIAN — kompensasi berikut ikut gagal, state mungkin "
                f"tidak pulih penuh: {'; '.join(undo_errors)}."
            )
        raise _curator.CuratorError(detail) from exc
    _drop_cached_review_plan(identity)
    return plan


def get_change_log(
    identity: str,
    ledger_path: Path | str | None = None,
) -> list[dict]:
    """Baca seluruh change log review untuk identity (daftar record dict)."""
    path = _review_ledger_path(identity, ledger_path)
    records: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return records
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def rollback_change(
    change_id: str,
    identity: str,
    skills_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
    curator_ledger_path: Path | str | None = None,
    priority_dir: Path | str | None = None,
) -> str:
    """Batalkan satu perubahan review berdasarkan id change log-nya.

    - Perubahan prioritas (set_priority/promote/demote) → kembalikan ke
      ``previous_level`` yang tercatat.
    - ``archive`` → ``curator.restore()`` mengembalikan skill dari ``.archive/``.
    - Tiap rollback dicatat lagi sebagai aksi ``rollback`` di change log.

    Mengembalikan pesan hasil yang jelas; melempar ``CuratorError`` yang
    informatif bila change_id tidak dikenal atau rollback gagal (tidak
    pernah gagal diam-diam).
    """
    review_ledger = _review_ledger_path(identity, ledger_path)
    target = None
    for record in get_change_log(identity, ledger_path):
        if record.get("id") == change_id:
            target = record
            break
    if target is None:
        raise _curator.CuratorError(
            f"change_id tidak dikenal: {change_id!r}. "
            "Lihat daftar perubahan lewat get_change_log()."
        )
    action = target.get("action")
    skill = target.get("skill", "?")
    previous_state = target.get("previous_state") or {}

    if action in ("set_priority", "promote", "demote"):
        if "previous_level" not in previous_state:
            raise _curator.CuratorError(
                f"Record {change_id!r} tidak menyimpan previous_level; "
                "rollback prioritas tidak bisa dilakukan dengan aman."
            )
        previous_level = previous_state["previous_level"]
        change = set_priority(
            skill,
            identity,
            previous_level,
            reason=f"rollback {change_id}",
            priority_dir=priority_dir,
            ledger_path=review_ledger,
        )
        _curator.log_action(
            "rollback",
            skill,
            {
                "reason": f"rollback {change_id}",
                "previous_state": {
                    "rolled_back_change": change_id,
                    "restored_level": previous_level,
                },
            },
            ledger_path=review_ledger,
        )
        return (
            f"Prioritas '{skill}' dikembalikan ke level "
            f"{previous_level} (rollback {change['id']} dari {change_id})."
        )
    if action == "archive":
        root = _curator._resolve_skills_dir(skills_dir)
        dst = _curator.restore(
            skill, skills_dir=root, ledger_path=curator_ledger_path
        )
        _curator.log_action(
            "rollback",
            skill,
            {
                "reason": f"rollback {change_id}",
                "previous_state": {
                    "rolled_back_change": change_id,
                    "restored_to": str(dst),
                },
            },
            ledger_path=review_ledger,
        )
        return f"Skill '{skill}' dikembalikan dari arsip ke {dst}."
    raise _curator.CuratorError(
        f"Aksi {action!r} pada record {change_id!r} tidak bisa di-rollback "
        "oleh modul ini."
    )
