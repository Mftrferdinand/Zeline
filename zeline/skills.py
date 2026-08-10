"""Skill system Zeline dengan scope public vs private.

Zeline dapat berjalan sebagai bot publik. Maka prosedur yang dibuat pemilik
(misalnya berisi path internal atau runbook privat) tidak boleh otomatis
tersedia untuk orang yang chat bot.

Layout data user:

    ~/.zeline/skills/
      public/     # skill bawaan aman yang dapat dibaca gateway `safe`
      private/    # skill pemilik; hanya profile `full` CLI owner

Install legacy memakai ``~/.zeline/skills/*.md``. Saat pertama
kali modul ini dipakai, skill legacy dipindahkan ke ``private/`` secara
konservatif agar tidak ada prosedur lama yang tidak sengaja terekspos.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from zeline import config

SKILLS_ROOT = config.DATA_DIR / "skills"
PUBLIC_SKILLS_DIR = SKILLS_ROOT / "public"
PRIVATE_SKILLS_DIR = SKILLS_ROOT / "private"
MIGRATION_MARKER = SKILLS_ROOT / ".scope-migrated-v1"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _safe_name(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "-")
    normalized = "".join(char for char in normalized if char.isalnum() or char in "-_")
    if not _NAME_RE.fullmatch(normalized):
        raise ValueError("nama skill harus 1–64 karakter: huruf, angka, - atau _")
    return normalized


def _chmod_private(path: Path, mode: int = 0o700) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _migrate_legacy_root() -> None:
    """Pindah markdown root lama ke private, tanpa menghapus data pemilik."""
    if MIGRATION_MARKER.exists():
        return
    for old_path in SKILLS_ROOT.glob("*.md"):
        name = old_path.stem
        destination = PRIVATE_SKILLS_DIR / old_path.name
        if destination.exists():
            # Tidak pernah overwrite: beri nama baru deterministik-ish.
            suffix = int(time.time() * 1000)
            destination = PRIVATE_SKILLS_DIR / f"{name}-legacy-{suffix}.md"
        old_path.replace(destination)
        _chmod_private(destination, 0o600)
    MIGRATION_MARKER.write_text("scoped skill migration complete\n", encoding="utf-8")
    _chmod_private(MIGRATION_MARKER, 0o600)


def _ensure_dirs() -> None:
    for directory in (SKILLS_ROOT, PUBLIC_SKILLS_DIR, PRIVATE_SKILLS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        _chmod_private(directory)
    _migrate_legacy_root()


def seed_skills() -> int:
    """Copy bundled public skill tanpa menimpa skill yang sudah dikustom."""
    _ensure_dirs()
    source = Path(__file__).resolve().parent / "skills"
    if not source.exists():
        return 0
    copied = 0
    for item in source.glob("*.md"):
        destination = PUBLIC_SKILLS_DIR / item.name
        if not destination.exists():
            destination.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            _chmod_private(destination, 0o600)
            copied += 1
    return copied


def _parse(markdown: str) -> tuple[str, str]:
    title, description = "", ""
    for line in markdown.splitlines():
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
        elif not description and stripped.startswith(">"):
            description = stripped.lstrip("> ").strip()
        if title and description:
            break
    return title, description


def list_skill_entries(include_private: bool = True) -> list[tuple[str, str, str, str]]:
    """Return ``(scope, name, title, description)`` entries."""
    _ensure_dirs()
    result: list[tuple[str, str, str, str]] = []
    locations: list[tuple[str, Path]] = [("public", PUBLIC_SKILLS_DIR)]
    if include_private:
        locations.append(("private", PRIVATE_SKILLS_DIR))
    for scope, directory in locations:
        for item in sorted(directory.glob("*.md")):
            title, description = _parse(item.read_text(encoding="utf-8", errors="replace"))
            result.append((scope, item.stem, title or item.stem, description or "(tanpa deskripsi)"))
    return result


def list_skills(include_private: bool = True) -> list[tuple[str, str, str]]:
    """Compatibility helper: list name/title/description without scope."""
    return [(name, title, description) for _scope, name, title, description in list_skill_entries(include_private)]


def _find_skill(name: str, include_private: bool) -> Path | None | str:
    """Find one safe stem; return error string on ambiguous fuzzy matching."""
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    # Owner private version overrides public version with same name.
    directories = [PUBLIC_SKILLS_DIR]
    if include_private:
        directories.insert(0, PRIVATE_SKILLS_DIR)
    for directory in directories:
        exact = directory / f"{normalized}.md"
        if exact.is_file():
            return exact
    candidates: list[Path] = []
    for directory in directories:
        candidates.extend(path for path in directory.glob("*.md") if normalized in path.stem.lower())
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return "ERROR skill ambigu: " + ", ".join(path.stem for path in candidates[:8])
    return None


def load_skill(name: str, include_private: bool = False) -> str:
    """Muat skill scope yang diizinkan. Input bukan path filesystem."""
    _ensure_dirs()
    found = _find_skill(name, include_private=include_private)
    if isinstance(found, str):
        return found
    if found is None:
        try:
            normalized = _safe_name(name)
        except ValueError:
            normalized = name.strip()
        return f"ERROR: skill '{normalized}' tidak ditemukan."
    return found.read_text(encoding="utf-8", errors="replace")


def save_skill(name: str, content: str) -> str:
    """Simpan skill pemilik di private scope. Hanya tool profile full memanggil ini."""
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    if not content.strip():
        return "ERROR skill: isi kosong."
    if len(content) > 100_000:
        return "ERROR skill: isi terlalu panjang (maksimum 100.000 karakter)."
    _ensure_dirs()
    target = PRIVATE_SKILLS_DIR / f"{normalized}.md"
    target.write_text(content, encoding="utf-8")
    _chmod_private(target, 0o600)
    return f"OK, skill private '{normalized}' disimpan."


def skills_block(include_private: bool = False) -> str:
    """Daftar token-cheap untuk system prompt sesuai otorisasi session."""
    available = list_skill_entries(include_private=include_private)
    if not available:
        return ""
    lines = "\n".join(
        f"- {name}: {description}" if scope == "public" else f"- {name} [private]: {description}"
        for scope, name, _title, description in available
    )
    return "\n\n## Skill yang tersedia (panggil load_skill untuk isi lengkap):\n" + lines
