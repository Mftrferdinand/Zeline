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

import hashlib
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

# Renamed or obsolete bundled skills. Delete only byte-identical seeded copies;
# user-customized files with these names survive. Each entry lists every digest
# the file ever shipped with, so a copy seeded from any past release is cleaned
# up. The 60 ``superagent-v7-sk*`` entries were renamed to ``zeline-zenith-sk*``.
LEGACY_BUNDLED_SKILL_DIGESTS: dict[str, tuple[str, ...]] = {
    "tmdb-media-web-maintenance.md": (
        "35f51a79be0c313bec2ec3f014200a00beeee7938173b5a20ccae0e5b62b8a4d",
    ),
    "superagent-v7-sk0.md": (
        "c340800fdb3008df0c3c8e42d9cbfc6be5a916eeb94dba25df4aee946e588517",
    ),
    "superagent-v7-sk1.md": (
        "88d64749ff3487bb3bba4e2673a1656bf3519db96fc72cbe67f948a66b90b720",
    ),
    "superagent-v7-sk2.md": (
        "9064595b2c3c7102ea9886d8df69244e2bca1cd252302f76b1399ca361c03a29",
        "ecd8927ec170d79a3716342a7aa4706214527ad65b25b58385c553c95626f0c5",
    ),
    "superagent-v7-sk3.md": (
        "b7e4f423e315fb2df410600b6efdf7904e726cff0198904cc8ed687e13d3f69c",
    ),
    "superagent-v7-sk4.md": (
        "e6b0ff2276e1222963ded51ec61a6e7cb832693671dbdb86712f57bb2d212ad5",
    ),
    "superagent-v7-sk5.md": (
        "59e0f5fbbb0687f3df91e5da2142d3bc72017da159b58534430385c6807978ee",
    ),
    "superagent-v7-sk6.md": (
        "20c292e4bf199407c060892ee6fa6cd92495a9b37f674659d3c91d5648b54951",
    ),
    "superagent-v7-sk7.md": (
        "4747b8dfcd365d40f91d92c525d6266d3dac300172f6a5312076e43a5f95481e",
    ),
    "superagent-v7-sk8.md": (
        "d010c39f7c5eb89c7397dda1af7e6936e5c23a03c5dd6e201706d1e40589a957",
    ),
    "superagent-v7-sk9.md": (
        "9ad4d8d38c808735b04581d5a06bb5c959a0cce292fbc11c3d84af106f9e306f",
    ),
    "superagent-v7-sk10.md": (
        "a3b687b5f56c91f3529c05cfebc66116b3854a7203a9ed0ba2067903280ba2d8",
    ),
    "superagent-v7-sk11.md": (
        "af1725ca642794811e73d077d48759989b78721aa85ec3df18e9d0dd5b07de5e",
        "d8475fb9e46a11b90b4a87492e57e33d84d800806b66ccf06edd175e709a0f42",
    ),
    "superagent-v7-sk12.md": (
        "8741f448ed787ef0651e92e219d914dad2d048b0ec894179c8f3c4779362417e",
    ),
    "superagent-v7-sk13.md": (
        "47c049698a3e0cedd5be7ce16c562f6b1f239ba6eb4bbfb807393c3ad57a00b4",
    ),
    "superagent-v7-sk14.md": (
        "675902ec570e62a585ba33f1f62f8c78d6f03d221550f51ff1f132b88fa204ce",
    ),
    "superagent-v7-sk15.md": (
        "71bd648055600c94c9e52941e26cec7bdde951cb5c0b8ba6f50586a8c01f56ab",
        "cffae84ee902e6fc7042c95a7985e3d30debbb7936b323cbb6030f74b3015518",
    ),
    "superagent-v7-sk16.md": (
        "6b09c2b1107601ac20df4530b5ec7f07f859465d3e3d8b791a9db57772e69602",
    ),
    "superagent-v7-sk17.md": (
        "0ec5c5e83dbeecb9d3356f1d6d4ef3950092158020fe6d699ee54b7fa3c8774d",
        "877f08cbb89ab01581bfc8d602e399a2bcebaa81325f77e6fad2ea0391b8faca",
    ),
    "superagent-v7-sk18.md": (
        "a95d8a757585d05b5cd77dd9f1478a4049674547585679ef74aeeb734944bb0f",
    ),
    "superagent-v7-sk19.md": (
        "7b0f46ff112c194839debbe419757c747ed60213b40d1a1e00d2eef58ee77a80",
    ),
    "superagent-v7-sk20.md": (
        "fc913be82f17d3c863e991b0da2ea6950028564669f4f2a613530b9de7525359",
    ),
    "superagent-v7-sk21.md": (
        "28687ba4f334cf032a52da710b747069619a83d8865f88987c01b0942bae39df",
    ),
    "superagent-v7-sk22.md": (
        "9ec5afd003ac4cd8abb3120953f3c5e2086755036da7d735dd5d7fa5d18823c4",
    ),
    "superagent-v7-sk23.md": (
        "42f2ec8f191cc17ff83d462c87e3df756519c44f64c594cad6ac0c2737c0a219",
    ),
    "superagent-v7-sk24.md": (
        "45cfb2516cdf759ff4ff83ebbfa8308adbfda71291158bbf9e4daa8520939597",
    ),
    "superagent-v7-sk25.md": (
        "f97142650a19039417e5a05cffca1deb1d236efc7b5e51178086d67e87cc5097",
    ),
    "superagent-v7-sk26.md": (
        "f81c12870cc23e232446424b5f8d285c4732c52fe579f3100f3280fb2f628ddd",
    ),
    "superagent-v7-sk27.md": (
        "cba23be330ffa9be02129fb3a9963572e4a79fa148ec71c886299237d3f22e11",
    ),
    "superagent-v7-sk28.md": (
        "a1be80bdf3c5f862bbe035579a638e384bec676ac453150d9d97409f816238c9",
    ),
    "superagent-v7-sk29.md": (
        "b1382e23fcf8c5242d673eecae8a590c4c3421f24051f4fc5f060575da9e926b",
    ),
    "superagent-v7-sk30.md": (
        "dbd55c2a39b5fe166b63a3ae22f80a2ac723193fb67f396dd51b39ada9e9d519",
    ),
    "superagent-v7-sk31.md": (
        "8603ee38f9ad689769e7705f8aab7470805d89ca07b984892bb0ab23aeee522f",
    ),
    "superagent-v7-sk32.md": (
        "9adaacf0a2f81454608e34f68d9a94a35fd2440658015243808587ac1f97fd6c",
    ),
    "superagent-v7-sk33.md": (
        "58a526f9209c24a5a6253b3a8ac757a47e9be159a7804338d0c9757a93f673fa",
    ),
    "superagent-v7-sk34.md": (
        "98d3df0c8dca86e7bbb4dccc963ec7d69485c40e6540e5cf2ea3f5311258b429",
    ),
    "superagent-v7-sk35.md": (
        "e4d7454603f848bcd380372b93120213aedc0cce807f52728a21b8724667003e",
    ),
    "superagent-v7-sk36.md": (
        "b52eac809bde2e4aa0798103e5d9dd5d2b67f1e517ac22d5498aabbe577ec1b2",
    ),
    "superagent-v7-sk37.md": (
        "b21b21eeec547c2c23a0368264da067564a506673c0711a3528d405209568adf",
    ),
    "superagent-v7-sk38.md": (
        "99293097066017e7208642a23116a986383cf22951bead844ba4e8d95dc07c8f",
    ),
    "superagent-v7-sk39.md": (
        "7e11f0da3fdf0dff2aa5b89adeaf4009c30b2d6ac5004dc0ac38d75069e697a6",
    ),
    "superagent-v7-sk40.md": (
        "048f511e02bf3894ff9936d73b9836a7401c4e5acf33fd8290d1ec5844bdd606",
    ),
    "superagent-v7-sk41.md": (
        "26a000f9324e69378c572ad24c05c674868c6f8c3bf420f73fe84d19498a738b",
    ),
    "superagent-v7-sk42.md": (
        "779f06ddd34ba0b877cf9582ac70de275412351613b5b2be5bbde09d21878440",
    ),
    "superagent-v7-sk43.md": (
        "ede5343a294f5824919d7dde4a8cf3542cbc3594dc0cf7762ce654597abe8e9e",
    ),
    "superagent-v7-sk44.md": (
        "6c9676d94a65578f834ab02f3329183026bc87e38eed2d3c654bec9286abfc3c",
    ),
    "superagent-v7-sk45.md": (
        "af81d46d749a027d5bf57be1ef1d3d9ed3767151e1ef7a98738fda0e15f128b5",
    ),
    "superagent-v7-sk46.md": (
        "74e2b688472b0f786eb7ca2682b9eee90b540ff8f65f5cd335a1f8f7f9766e86",
    ),
    "superagent-v7-sk47.md": (
        "8e8f7d0638e906edbd59f791b73b2b3db63396d8e35657452be170188986ef39",
    ),
    "superagent-v7-sk48.md": (
        "16eca2174799c055148706344f8f6d7caad8b675e08f594f7b51d45a11bf2cc8",
    ),
    "superagent-v7-sk49.md": (
        "f748aa8393b393bae32fd5eed43d3b092d775727fda040b6fa2fe63f53c058a0",
    ),
    "superagent-v7-sk50.md": (
        "3ad7719b8dc49daa51b71a394209b56370be1f3f9ef0408a7fd7f15bdec37a90",
    ),
    "superagent-v7-sk51.md": (
        "7e8e6dd5150a3f5274616fc53dee492ecdd778e3cbcb213ad87d7f0227013979",
    ),
    "superagent-v7-sk52.md": (
        "0569220a265b05b42bf80e64951e7697659455f7afb7205b98abc6d367047304",
    ),
    "superagent-v7-sk53.md": (
        "b9c7458e3f3b25d45c8e564f5cea95147afe036086a27cbf659443b83e983344",
    ),
    "superagent-v7-sk54.md": (
        "1d96c2d0f3f44e8dc2248c58f051c2693aeca0b935a0055c993aa1332b382444",
        "5cb68ad3511d528b8efc8fa02fc147c37679fd6a6080173d47334995633f2732",
    ),
    "superagent-v7-sk55.md": (
        "e5359af0e7881abd4a42f5f9cb612ff7a8bc459bf56291ab08a41e4dc8e9dfd0",
    ),
    "superagent-v7-sk56.md": (
        "55806f631f4c493c592f192517ff7308bfeae50ef6359934f75d19d9ded45243",
    ),
    "superagent-v7-sk57.md": (
        "b3182f0c278b0ab9f6d76aa393b75a48d352fda6794414c936ec6a764e894c9c",
    ),
    "superagent-v7-sk58.md": (
        "1a52d122f88d48a28d7246a98141629784f8bb38d17c71b5cc4981092b13c6d4",
    ),
    "superagent-v7-sk59.md": (
        "06fe8db77f98029c485042f4c4a575ac8a0b9f4ee7746528dc2d4594f24e694d",
    ),
}

# Bundled skills whose content was corrected in-place (not renamed). seed_skills()
# never overwrites existing files, so without this map a user on the pre-fix
# revision keeps the stale copy forever. Each entry lists every digest the file
# shipped with before the fix, on both POSIX (LF) and Windows (CRLF) line
# endings — seed_skills() compares bytes, and a CRLF checkout hashes differently.
# User-customized files (any digest not listed here) are always preserved.
BUNDLED_SKILL_UPDATE_DIGESTS: dict[str, tuple[str, ...]] = {
    "zeline-zenith-sk0.md": (
        # origin/main pre-fix, LF
        "577d36a35e97b4c461e769c723dc4a6187e99dd4646c5584f59e7d759be67a09",
        # same content with CRLF (Windows checkout)
        "629a599da79b90c6016d739ba19fe70afb8d7d79d56649507ef36d2767c7ba9a",
    ),
    "zeline-zenith-sk52.md": (
        # origin/main pre-fix, LF
        "9afdaf5bf7613db366046418fb07cc90952ece1734c88d2aa4e839fefa39f0e6",
        # same content with CRLF (Windows checkout)
        "3bc375a999d48666cf809245298864710879ba4a6a799a617595ddec06114b78",
    ),
}


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


def _remove_unmodified_legacy_bundled_skills() -> None:
    """Remove obsolete seeded copies without deleting user customizations."""
    public_root = PUBLIC_SKILLS_DIR.resolve()
    for name, expected_digests in LEGACY_BUNDLED_SKILL_DIGESTS.items():
        # Migration entries are flat bundled Markdown filenames, never paths.
        if Path(name).name != name or not name.endswith(".md"):
            continue
        path = PUBLIC_SKILLS_DIR / name
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve(strict=False)
            # Windows runners may spell one directory as DOS 8.3 vs canonical
            # long path. Compare filesystem identity, not path-string spelling.
            if not resolved.parent.samefile(public_root) or not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in expected_digests:
                path.unlink()
        except OSError:
            pass


def _refresh_known_bundled_revisions(source: Path) -> int:
    """Overwrite seeded copies that still match a known pre-fix revision.

    seed_skills() never overwrites existing files, which is the right default
    for user customizations. But when a bundled skill is corrected in-place
    (not renamed), a user on the old revision keeps the stale copy forever.
    This hook removes that copy before seeding so the fresh source replaces it.

    Only files whose current bytes match a digest in BUNDLED_SKILL_UPDATE_DIGESTS
    are touched. A user-customized file (any other digest) is always preserved.
    Returns the number of stale copies removed.
    """
    if not BUNDLED_SKILL_UPDATE_DIGESTS:
        return 0
    public_root = PUBLIC_SKILLS_DIR.resolve()
    removed = 0
    for name, expected_digests in BUNDLED_SKILL_UPDATE_DIGESTS.items():
        # Only refresh files that actually ship from the current source.
        if not (source / name).is_file():
            continue
        path = PUBLIC_SKILLS_DIR / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=False)
            if not resolved.parent.samefile(public_root):
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in expected_digests:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def seed_skills(source: str | Path | None = None) -> int:
    """Salin skill bawaan dari paket ke scope public tanpa overwrite.

    Mendukung dua bentuk sumber:
    - flat  : ``skills/<name>.md``
    - folder : ``skills/<name>/SKILL.md`` (+ references/scripts/assets)

    ``source`` is injectable for importers/tests so they never need to mutate
    the installed package directory. Normal callers use the bundled skills.
    """
    _ensure_dirs()
    _remove_unmodified_legacy_bundled_skills()
    source = Path(source).expanduser().resolve() if source is not None else Path(__file__).resolve().parent / "skills"
    if not source.exists():
        return 0
    _refresh_known_bundled_revisions(source)
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
    # 2) Alias pendek korpus Zenith. Registry bawaan menggunakan ``sk0`` …
    # ``sk59``, sedangkan nama file publiknya memakai prefix
    # ``zeline-zenith-``. Tangani ini sebelum fuzzy matching agar ``sk1``
    # tidak berbenturan dengan ``sk10`` … ``sk19``.
    if re.fullmatch(r"sk(?:[0-9]|[1-5][0-9])", normalized):
        for directory in directories:
            canonical = directory / f"zeline-zenith-{normalized}.md"
            if canonical.is_file():
                return canonical
    # 3) fuzzy match berdasar nama unit skill.
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
            # POSIX-style separators keep the listing stable across platforms;
            # on Windows ``relative_to`` yields backslashes, which broke both the
            # docs the agent reads back and the tests asserting on this text.
            listing = "\n".join(
                f"- {skill_dir.as_posix()}/{Path(rel).as_posix()}" for rel in extras
            )
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
