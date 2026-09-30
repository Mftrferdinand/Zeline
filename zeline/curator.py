"""Skill curator Zeline: pindai, arsipkan, dan pulihkan skill milik user.

Zeline bisa membuat skill sendiri lewat ``manage_skill``, tapi tidak ada yang
merawatnya — lama-lama skill menumpuk dan basi. Modul ini memindai direktori
skill user, menandai skill yang sudah lama tidak disentuh atau yang
deskripsinya duplikat, lalu mengarsipkannya ke ``.archive/`` (bisa di-restore
kapan saja) sambil mencatat semua aksi di ledger JSONL.

Default yang dipakai adalah skill *private* milik user
(``~/.zeline/skills/private``), bukan skill public bawaan — skill bawaan
tidak boleh disentuh curator.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from zeline import skills as _skills

#: Nama direktori arsip di dalam skills_dir.
ARCHIVE_DIR_NAME = ".archive"
#: Nama file ledger (satu baris JSON per aksi).
LEDGER_FILE_NAME = "curator-ledger.jsonl"
#: Batas umur default: skill lebih tua dari ini dianggap basi.
DEFAULT_STALE_DAYS = 90
#: Panjang prefix deskripsi untuk heuristik duplikat.
DUPLICATE_PREFIX_LEN = 40


class CuratorError(Exception):
    """Kegagalan operasi curator yang bisa dijelaskan ke user."""


def _default_skills_dir() -> Path:
    """Direktori skill user (private); konstanta diambil dari zeline.skills."""
    return _skills.PRIVATE_SKILLS_DIR


def _default_ledger_path() -> Path:
    return _skills.SKILLS_ROOT / LEDGER_FILE_NAME


def _resolve_skills_dir(skills_dir: Path | str | None) -> Path:
    return Path(skills_dir).expanduser() if skills_dir else _default_skills_dir()


def _resolve_ledger_path(ledger_path: Path | str | None) -> Path:
    return Path(ledger_path).expanduser() if ledger_path else _default_ledger_path()


def _check_name(name: str) -> str:
    """Tolak nama yang bisa dipakai untuk path traversal."""
    if not name or name in (".", "..") or name.startswith("."):
        raise CuratorError(f"Nama skill tidak valid: {name!r}")
    if "/" in name or "\\" in name or os.sep in name:
        raise CuratorError(f"Nama skill tidak valid: {name!r}")
    return name


def _parse_frontmatter(text: str) -> tuple[str, str]:
    """Ambil (name, description) dari frontmatter YAML di SKILL.md.

    Parser mandiri yang sederhana (tidak bergantung ke helper privat
    zeline.skills) supaya curator tetap bisa jalan sendiri.
    """
    name, description = "", ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            stripped = line.strip()
            if stripped == "---":
                break
            key, sep, value = stripped.partition(":")
            if not sep:
                continue
            key = key.strip().lower()
            value = value.strip().strip("\"'")
            if key == "name" and not name:
                name = value
            elif key == "description" and not description:
                description = value
    return name, description


def _skill_mtime(skill_dir: Path) -> float:
    """mtime terbaru di seluruh isi direktori skill (edit file = disentuh)."""
    latest = skill_dir.stat().st_mtime
    for root, _dirs, files in os.walk(skill_dir):
        for fname in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, fname))
            except OSError:
                continue
            if mtime > latest:
                latest = mtime
    return latest


def _skill_size_kb(skill_dir: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(skill_dir):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(root, fname))
            except OSError:
                continue
    return round(total / 1024, 1)


def _duplicate_key(description: str) -> str:
    return description[:DUPLICATE_PREFIX_LEN].casefold().strip()


def scan(
    skills_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> list[dict]:
    """Pindai semua skill user; kembalikan info per skill.

    Tiap dict berisi: name, description, mtime (iso), age_days, size_kb,
    stale (bool), possible_duplicates (list nama skill lain).
    ``ledger_path`` diterima demi konsistensi API tapi tidak dipakai di sini.
    """
    del ledger_path  # tidak dipakai saat scan
    root = _resolve_skills_dir(skills_dir)
    entries: list[dict] = []
    if not root.is_dir():
        return entries
    now = datetime.now(timezone.utc).timestamp()
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_md = child / _skills.SKILL_ENTRY
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm_name, description = _parse_frontmatter(text)
        mtime = _skill_mtime(child)
        age_days = (now - mtime) / 86400
        entries.append(
            {
                "name": fm_name or child.name,
                "description": description,
                "mtime": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                "age_days": round(age_days, 1),
                "size_kb": _skill_size_kb(child),
                "stale": age_days > stale_days,
                "possible_duplicates": [],
            }
        )
    # Heuristik duplikat murah: 40 karakter pertama deskripsi sama persis.
    by_prefix: dict[str, list[str]] = {}
    for entry in entries:
        prefix = _duplicate_key(entry["description"])
        if prefix:
            by_prefix.setdefault(prefix, []).append(entry["name"])
    for entry in entries:
        prefix = _duplicate_key(entry["description"])
        others = [n for n in by_prefix.get(prefix, []) if n != entry["name"]]
        entry["possible_duplicates"] = others
    return entries


def _append_ledger(ledger_path: Path, record: dict) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _archive_root(skills_dir: Path) -> Path:
    root = skills_dir / ARCHIVE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _archived_matches(name: str, archive_root: Path) -> list[Path]:
    """Daftar arsip untuk ``name``, terurut dari yang terlama."""
    if not archive_root.is_dir():
        return []
    prefix = f"{name}-"
    matches = [
        p
        for p in archive_root.iterdir()
        if p.is_dir() and p.name.startswith(prefix)
    ]
    return sorted(matches, key=lambda p: p.name)


def archive(
    name: str,
    skills_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> Path:
    """Pindahkan skill ke ``.archive/<nama>-<YYYYMMDD-HHMMSS>`` + catat ledger."""
    name = _check_name(name)
    root = _resolve_skills_dir(skills_dir)
    ledger = _resolve_ledger_path(ledger_path)
    src = root / name
    archive_root = _archive_root(root)
    if not src.is_dir() or not (src / _skills.SKILL_ENTRY).is_file():
        already = _archived_matches(name, archive_root)
        if already:
            raise CuratorError(
                f"Skill '{name}' sudah di-archive "
                f"(lihat {already[-1].name}); pakai restore() untuk mengembalikannya."
            )
        raise CuratorError(f"Skill '{name}' tidak ditemukan di {root}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = archive_root / f"{name}-{stamp}"
    counter = 1
    while dst.exists():
        counter += 1
        dst = archive_root / f"{name}-{stamp}-{counter}"
    shutil.move(str(src), str(dst))
    _append_ledger(
        ledger,
        {"action": "archive", "name": name, "src": str(src), "dst": str(dst)},
    )
    return dst


def restore(
    name: str,
    skills_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> Path:
    """Kembalikan arsip terbaru ``name`` dari ``.archive/`` ke skills_dir."""
    name = _check_name(name)
    root = _resolve_skills_dir(skills_dir)
    ledger = _resolve_ledger_path(ledger_path)
    archive_root = _archive_root(root)
    matches = _archived_matches(name, archive_root)
    if not matches:
        raise CuratorError(
            f"Tidak ada arsip untuk skill '{name}' di {archive_root}"
        )
    src = matches[-1]  # arsip terbaru
    dst = root / name
    if dst.exists():
        raise CuratorError(
            f"'{name}' sudah ada di {root}; restore dibatalkan supaya tidak menimpa."
        )
    shutil.move(str(src), str(dst))
    _append_ledger(
        ledger,
        {"action": "restore", "name": name, "src": str(src), "dst": str(dst)},
    )
    return dst


def prune(
    stale_days: int = DEFAULT_STALE_DAYS,
    apply: bool = False,
    skills_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> list[dict]:
    """Rencana arsip untuk skill basi.

    Tanpa ``apply=True`` ini hanya dry-run (tidak ada yang dipindah).
    Dengan ``apply=True`` tiap skill basi di-archive(); tiap dict hasil
    mendapat kunci tambahan ``archived_to``.
    """
    root = _resolve_skills_dir(skills_dir)
    plan = [
        entry
        for entry in scan(root, ledger_path, stale_days)
        if entry["stale"]
    ]
    if not apply:
        return plan
    for entry in plan:
        dst = archive(entry["name"], skills_dir=root, ledger_path=ledger_path)
        entry["archived_to"] = str(dst)
    return plan
