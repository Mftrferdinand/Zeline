"""Self-improving learning loop.

After completing a complex task, the agent can distill the experience into
a reusable skill via ``learn_skill``. Learned skills are stored as Markdown
in ``~/.zeline/skills/learned/`` and loaded like any other skill.

This is the "closed learning loop": experience -> skill -> reuse -> improve.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path


def learned_dir() -> Path:
    d = Path.home() / ".zeline" / "skills" / "learned"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # M1 fix: truncate to avoid OSError on long filenames (255 char limit)
    slug = slug[:100]
    return slug or f"skill-{int(time.time())}"


def save_learned_skill(name: str, description: str, content: str) -> str:
    """Save a learned skill in SKILL.md format (agentskills.io compatible).

    Creates a skill directory with:
    - SKILL.md: lean core (name, description, when to use, quick steps)
    - references/: detailed documentation (from content)
    - scripts/: empty (agent can add helper scripts later)

    Returns the skill directory path.
    """
    slug = _slugify(name)
    skill_dir = learned_dir() / slug
    # H2 fix: use microseconds + pid to avoid same-second collision
    if skill_dir.exists():
        skill_dir = learned_dir() / f"{slug}-{int(time.time() * 1000000)}-{os.getpid()}"
        # Still exists (extreme edge)? add counter
        counter = 0
        while skill_dir.exists():
            counter += 1
            skill_dir = learned_dir() / f"{slug}-{int(time.time() * 1000000)}-{os.getpid()}-{counter}"
    skill_dir.mkdir(parents=True, exist_ok=True)
    # L2 fix: restrict directory permissions too
    skill_dir.chmod(0o700)
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "references").chmod(0o700)
    (skill_dir / "scripts").mkdir(exist_ok=True)
    (skill_dir / "scripts").chmod(0o700)

    # M2 fix: sanitize newlines to prevent markdown/header injection
    safe_name = str(name).replace("\n", " ").replace("\r", " ").strip()[:200]
    safe_desc = str(description).replace("\n", " ").replace("\r", " ").strip()[:500]
    # L1 fix: truncate at line boundary to avoid breaking code fences
    raw_content = content.strip()
    if len(raw_content) > 500:
        cut = raw_content[:500]
        last_nl = cut.rfind("\n")
        quick = cut[:last_nl] if last_nl > 200 else cut
    else:
        quick = raw_content

    # SKILL.md: lean core per agentskills.io standard
    skill_md = f"""# {safe_name}

> {safe_desc}

*Learned {time.strftime('%Y-%m-%d')} from agent experience.*

## When to use

See `references/details.md` for the full procedure.

## Quick steps

{quick}

## References

- `references/details.md` — full documentation
- `scripts/` — helper scripts (if any)
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "SKILL.md").chmod(0o600)

    # references/details.md: full content
    # M3 fix: use safe_name (was raw name with newline injection)
    (skill_dir / "references" / "details.md").write_text(
        f"# {safe_name} — Details\n\n{content.strip()}\n", encoding="utf-8"
    )
    (skill_dir / "references" / "details.md").chmod(0o600)

    # Hooks: skill learned (never raises; isolated with timeout)
    try:
        from zeline import hooks as _hooks

        _hooks.trigger(
            _hooks.ON_SKILL_LEARNED,
            {"name": safe_name, "path": str(skill_dir)},
        )
    except Exception:
        pass

    return str(skill_dir)


def list_learned_skills() -> list[dict]:
    """List all learned skills with name and description."""
    result = []
    # New format: skill directories with SKILL.md
    for skill_dir in sorted(learned_dir().iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        name = lines[0].lstrip("# ").strip() if lines else skill_dir.name
        desc = ""
        for line in lines[1:6]:
            if line.startswith(">"):
                desc = line.lstrip("> ").strip()
                break
        result.append({"name": name, "file": skill_dir.name, "description": desc})
    # Legacy format: single .md files (backwards compat)
    for md in sorted(learned_dir().glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        name = lines[0].lstrip("# ").strip() if lines else md.stem
        desc = ""
        for line in lines[1:6]:
            if line.startswith(">"):
                desc = line.lstrip("> ").strip()
                break
        result.append({"name": name, "file": md.name, "description": desc})
    return result


def improve_learned_skill(slug: str, addition: str) -> str:
    """Append an improvement note to an existing learned skill.

    Used when the agent discovers a better way or a gotcha for something
    it already learned. Returns the updated file path.
    Handles both new directory format (SKILL.md) and legacy .md files.
    """
    # Sanitize slug to prevent path traversal
    slug = _slugify(slug)
    # H1 fix: check directory format FIRST (new format)
    skill_dir = learned_dir() / slug
    # L1 fix: reject symlinks (could point outside learned_dir)
    if skill_dir.is_symlink():
        raise ValueError(f"Refusing to follow symlink: {slug!r}")
    if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
        skill_md = skill_dir / "SKILL.md"
        details_md = skill_dir / "references" / "details.md"
        update = f"\n\n## Update ({time.strftime('%Y-%m-%d')})\n\n{addition.strip()}\n"
        # Append to both SKILL.md and details.md
        text = skill_md.read_text(encoding="utf-8")
        skill_md.write_text(text.rstrip() + update, encoding="utf-8")
        if details_md.is_file():
            dtext = details_md.read_text(encoding="utf-8")
            details_md.write_text(dtext.rstrip() + update, encoding="utf-8")
        return str(skill_dir)
    # Check for timestamped directory variants
    matches = sorted(learned_dir().glob(f"{slug}-*"))
    for m in matches:
        # Reject symlinks (same as main branch)
        if m.is_symlink():
            continue
        if m.is_dir() and (m / "SKILL.md").is_file():
            skill_md = m / "SKILL.md"
            update = f"\n\n## Update ({time.strftime('%Y-%m-%d')})\n\n{addition.strip()}\n"
            text = skill_md.read_text(encoding="utf-8")
            skill_md.write_text(text.rstrip() + update, encoding="utf-8")
            return str(m)
    # Legacy: single .md file
    path = learned_dir() / f"{slug}.md"
    # Reject symlinks (write-through protection)
    if path.is_symlink():
        raise ValueError(f"Refusing to follow symlink: {slug!r}")
    if not path.exists():
        matches = list(learned_dir().glob(f"{slug}*.md"))
        if not matches:
            raise FileNotFoundError(f"No learned skill matching {slug!r}")
        path = matches[0]
    text = path.read_text(encoding="utf-8")
    update = (
        f"\n\n## Update ({time.strftime('%Y-%m-%d')})\n\n{addition.strip()}\n"
    )
    path.write_text(text.rstrip() + update, encoding="utf-8")
    return str(path)
