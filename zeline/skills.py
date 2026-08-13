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
    """Copy bundled public skill tanpa menimpa skill yang sudah dikustom.

    Mendukung dua bentuk sumber:
    - flat  : ``skills/<name>.md``
    - folder : ``skills/<name>/SKILL.md`` (+ references/scripts/assets)
    """
    _ensure_dirs()
    source = Path(__file__).resolve().parent / "skills"
    if not source.exists():
        return 0
    copied = 0
    # 1) skill flat: satu file .md langsung di root skills/
    for item in source.glob("*.md"):
        destination = PUBLIC_SKILLS_DIR / item.name
        if not destination.exists():
            destination.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            _chmod_private(destination, 0o600)
            copied += 1
    # 2) skill folder: subdirektori berisi SKILL.md (+ file pendukung).
    for sub in sorted(source.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "SKILL.md").is_file():
            continue
        destination = PUBLIC_SKILLS_DIR / sub.name
        if not destination.exists():
            _copy_skill_tree(sub, destination)
            copied += 1
    return copied


def _copy_skill_tree(src: Path, dst: Path) -> None:
    """Salin satu folder skill (SKILL.md + file pendukung) secara aman.

    Symlink diabaikan agar tidak ada path yang keluar dari folder tujuan.
    """
    for root, dirs, files in os.walk(src):
        # Jangan ikuti symlink direktori.
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        _chmod_private(target_dir)
        for name in files:
            source_file = Path(root) / name
            if source_file.is_symlink():
                continue
            target_file = target_dir / name
            try:
                target_file.write_bytes(source_file.read_bytes())
                _chmod_private(target_file, 0o600)
            except OSError:
                pass


def _parse(markdown: str) -> tuple[str, str]:
    title, description = "", ""
    lines = markdown.splitlines()
    # YAML frontmatter (skill folder standar): --- name: .. description: .. ---
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            stripped = lines[index].strip()
            if stripped == "---":
                break
            lower = stripped.lower()
            if not title and lower.startswith("name:"):
                title = stripped.split(":", 1)[1].strip().strip("\"'")
            elif not description and lower.startswith("description:"):
                description = stripped.split(":", 1)[1].strip().strip("\"'")
    # Format Zeline klasik: '# Judul' + '> deskripsi'
    for line in lines:
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
        elif not description and stripped.startswith(">"):
            description = stripped.lstrip("> ").strip()
        if title and description:
            break
    return title, description


def _iter_skill_units(directory: Path) -> list[tuple[str, Path]]:
    """Kembalikan ``(name, skill_md_path)`` untuk skill flat maupun folder."""
    units: list[tuple[str, Path]] = []
    if not directory.exists():
        return units
    for item in sorted(directory.glob("*.md")):
        units.append((item.stem, item))
    for sub in sorted(directory.iterdir()):
        if sub.is_dir() and (sub / "SKILL.md").is_file():
            units.append((sub.name, sub / "SKILL.md"))
    return units


def list_skill_entries(include_private: bool = True) -> list[tuple[str, str, str, str]]:
    """Return ``(scope, name, title, description)`` entries."""
    _ensure_dirs()
    result: list[tuple[str, str, str, str]] = []
    locations: list[tuple[str, Path]] = [("public", PUBLIC_SKILLS_DIR)]
    if include_private:
        locations.append(("private", PRIVATE_SKILLS_DIR))
    for scope, directory in locations:
        for name, skill_md in _iter_skill_units(directory):
            title, description = _parse(skill_md.read_text(encoding="utf-8", errors="replace"))
            result.append((scope, name, title or name, description or "(tanpa deskripsi)"))
    return result


def list_skills(include_private: bool = True) -> list[tuple[str, str, str]]:
    """Compatibility helper: list name/title/description without scope."""
    return [(name, title, description) for _scope, name, title, description in list_skill_entries(include_private)]


def _find_skill(name: str, include_private: bool) -> Path | None | str:
    """Find one safe skill; return error string on ambiguous fuzzy matching.

    Mengembalikan Path ke file .md (flat) atau ke SKILL.md di dalam folder.
    """
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    # Owner private version overrides public version with same name.
    directories = [PUBLIC_SKILLS_DIR]
    if include_private:
        directories.insert(0, PRIVATE_SKILLS_DIR)
    # 1) exact match: flat .md dulu, lalu folder/SKILL.md.
    for directory in directories:
        exact = directory / f"{normalized}.md"
        if exact.is_file():
            return exact
        folder = directory / normalized / "SKILL.md"
        if folder.is_file():
            return folder
    # 2) fuzzy match berdasar nama unit skill.
    candidates: list[Path] = []
    for directory in directories:
        for unit_name, skill_md in _iter_skill_units(directory):
            if normalized in unit_name.lower():
                candidates.append(skill_md)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = [p.parent.name if p.name == "SKILL.md" else p.stem for p in candidates[:8]]
        return "ERROR ambiguous skill: " + ", ".join(names)
    return None


def load_skill(name: str, include_private: bool = False) -> str:
    """Muat skill scope yang diizinkan. Input bukan path filesystem.

    Untuk skill folder, isi SKILL.md dikembalikan plus daftar file pendukung
    (references/scripts/assets) agar agent tahu file yang bisa dibaca.
    """
    _ensure_dirs()
    found = _find_skill(name, include_private=include_private)
    if isinstance(found, str):
        return found
    if found is None:
        try:
            normalized = _safe_name(name)
        except ValueError:
            normalized = name.strip()
        return f"ERROR: skill '{normalized}' not found."
    content = found.read_text(encoding="utf-8", errors="replace")
    # Skill folder: sertakan daftar file pendukung relatif ke folder skill.
    if found.name == "SKILL.md":
        skill_dir = found.parent
        extras: list[str] = []
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and path != found and not path.is_symlink():
                extras.append(str(path.relative_to(skill_dir)))
        if extras:
            listing = "\n".join(f"- {skill_dir}/{rel}" for rel in extras)
            content += (
                "\n\n---\n### Supporting files for this skill (read with read_file if needed):\n"
                + listing
            )
    return content


def save_skill(name: str, content: str) -> str:
    """Simpan skill pemilik di private scope. Hanya tool profile full memanggil ini."""
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    if not content.strip():
        return "ERROR skill: empty content."
    if len(content) > 100_000:
        return "ERROR skill: content too long (maximum 100,000 characters)."
    _ensure_dirs()
    target = PRIVATE_SKILLS_DIR / f"{normalized}.md"
    target.write_text(content, encoding="utf-8")
    _chmod_private(target, 0o600)
    return f"OK, private skill '{normalized}' saved."


def update_skill(name: str, old_text: str, new_text: str) -> str:
    """Patch satu bagian unik skill private milik operator."""
    _ensure_dirs()
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    target = PRIVATE_SKILLS_DIR / f"{normalized}.md"
    if not target.is_file():
        return f"ERROR: private skill '{normalized}' not found."
    content = target.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count != 1:
        return f"ERROR update skill: old_text must be unique (found {count})."
    target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    _chmod_private(target, 0o600)
    return f"Patched SKILL.md in skill '{normalized}' (1 replacement)."


def _short_desc(description: str, limit: int = 90) -> str:
    """Deskripsi ringkas 1-baris untuk daftar skill di system prompt.

    Daftar skill di-inject SETIAP turn; deskripsi panjang (ratusan char × 171
    skill ≈ 20k char) memboroskan token & memperlambat tiap request. Cukup
    kalimat pertama / potong di ~90 char — nama skill tetap bisa ditemukan,
    isi lengkap dibaca via load_skill saat relevan.
    """
    text = " ".join(str(description).split())
    # Ambil kalimat pertama bila pendek; kalau tidak, potong keras di limit.
    for sep in (". ", " — ", "; "):
        head = text.split(sep, 1)[0]
        if head and len(head) <= limit:
            return head
    return text[:limit].rstrip(" ,.—-") + ("…" if len(text) > limit else "")


def skills_block(include_private: bool = False) -> str:
    """Daftar token-cheap untuk system prompt sesuai otorisasi session."""
    available = list_skill_entries(include_private=include_private)
    if not available:
        return ""
    lines = "\n".join(
        f"- {name}: {_short_desc(description)}" if scope == "public" else f"- {name} [private]: {_short_desc(description)}"
        for scope, name, _title, description in available
    )
    return "\n\n## Available skills (call load_skill for full content):\n" + lines
