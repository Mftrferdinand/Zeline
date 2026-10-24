#!/usr/bin/env python3
"""Auto-generate accurate project metrics. Run: python3 -m zeline.zenith_tools.scripts.counts

Rewritten to walk the tree directly instead of shelling out to ``find``/``du``:
* no shell, so no injection surface and it works where only Python exists;
* a missing directory or unreadable file is counted/skipped, not a crash —
  the previous version died with ``ValueError: invalid literal for int()``
  whenever a subprocess returned empty output;
* symlinks are not followed, so an accidental symlink loop cannot hang it.
"""
from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent

TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".txt", ".sh"}


def iter_files():
    for path in root.rglob("*"):
        # rglob does not follow directory symlinks on Python 3.13+, but stay
        # explicit so older interpreters cannot loop either.
        if path.is_symlink():
            continue
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        yield path


files = [p for p in iter_files() if p.is_file()]

total_files = len(files)
md_files = sum(1 for p in files if p.suffix == ".md")
py_files = sum(1 for p in files if p.suffix == ".py")

total_loc = 0
for path in files:
    if path.suffix in TEXT_SUFFIXES:
        try:
            with path.open("rb") as handle:
                total_loc += sum(1 for _ in handle)
        except OSError:
            continue

# Skills count: standalone .md files + zeline directory
skill_md_files = sum(
    1 for p in files
    if p.suffix == ".md" and "skills" in p.parts and p.parent.name == "skills"
)
skills_count = skill_md_files + (1 if (root / "zeline" / "skills").is_dir() else 0)

# Tools count (python files excluding __init__.py)
tools_count = sum(
    1 for p in files
    if p.suffix == ".py" and p.name != "__init__.py" and "tools" in p.parts
)

# Test files
test_count = sum(
    1 for p in files
    if p.suffix == ".py" and (p.name.startswith("test_") or p.name.endswith("_test.py"))
)

# Directory size in KB
zip_size_kb = 0
for path in files:
    try:
        zip_size_kb += path.stat().st_size
    except OSError:
        continue
zip_size_kb //= 1024

result = {
    "total_files": total_files,
    "md_files": md_files,
    "py_files": py_files,
    "total_loc": total_loc,
    "skills_count": skills_count,
    "tools_count": tools_count,
    "test_count": test_count,
    "zip_size_kb": zip_size_kb,
}

print(json.dumps(result, indent=2))
