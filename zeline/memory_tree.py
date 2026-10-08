"""Memory Tree: Obsidian-compatible vault as a VIEW over Zeline's memory.

This module does NOT store new data. It renders the existing stores
(episodic memory, goals, learned skills, user model) as human-readable
Markdown files with Obsidian wikilinks, so the user can open the vault
in Obsidian, browse, and edit by hand.

Layout::

    vault/
      README.md            # index with links to everything
      daily/YYYY-MM-DD.md  # episodes grouped per day
      topics/<slug>.md     # topic pages aggregating episodes + goals
      skills/<slug>.md     # learned skill summaries (view, not the source)
      goals.md             # active goals
      user.md              # user model traits

``export_vault()`` writes everything. ``sync_vault()`` only rewrites
files whose rendered content changed (tracked via sha256 in
``~/.zeline/vault-sync.json``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VAULT_VERSION = 1


def vault_path() -> Path:
    """Configured vault path (VAULT_PATH), default ~/zeline-vault."""
    try:
        from zeline import config
        raw = str(getattr(config, "VAULT_PATH", "") or "").strip()
    except Exception:
        raw = ""
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "zeline-vault"


def _sync_state_path() -> Path:
    from zeline import config
    return Path(config.DATA_DIR) / "vault-sync.json"


def _load_sync_state() -> dict:
    try:
        return json.loads(_sync_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_sync_state(state: dict) -> None:
    p = _sync_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (slug or "untitled")[:max_len]


def _unique_rel(files: dict[str, str], rel: str) -> str:
    """Return ``rel`` or a ``-2``/``-3``-suffixed variant not yet in ``files``."""
    if rel not in files:
        return rel
    stem, dot, ext = rel.rpartition(".")
    i = 2
    while True:
        cand = f"{stem}-{i}{dot}{ext}" if dot else f"{rel}-{i}"
        if cand not in files:
            return cand
        i += 1


def _clean(text: str, limit: int = 0) -> str:
    """Strip control chars; optionally truncate."""
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t


def _fmt_date(ts: float) -> str:
    # A2-L8: tz-aware UTC instead of naive local time.
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _fmt_dt(ts: float) -> str:
    # A2-L8: tz-aware UTC instead of naive local time.
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Builders (pure: identity -> {relpath: content})
# ---------------------------------------------------------------------------

def _build_daily(identity: str) -> dict[str, str]:
    """daily/YYYY-MM-DD.md from episodic memory."""
    from zeline import memory as mem

    episodes = mem.list_episodes(identity, limit=200)
    by_day: dict[str, list[dict]] = {}
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        day = _fmt_date(ep.get("created_at", time.time()))
        by_day.setdefault(day, []).append(ep)

    files: dict[str, str] = {}
    for day in sorted(by_day):
        lines = [f"# {day}", ""]
        for ep in sorted(by_day[day], key=lambda e: e.get("created_at", 0)):
            title = _clean(ep.get("title", "Untitled"), 200)
            lines.append(f"## {title}")
            lines.append(f"*Created {_fmt_dt(ep.get('created_at', time.time()))}*")
            lines.append("")
            for i, ev in enumerate(ep.get("events", [])[:20], 1):
                lines.append(f"{i}. {_clean(ev, 500)}")
            lines.append("")
        files[f"daily/{day}.md"] = "\n".join(lines).rstrip() + "\n"
    return files


def _build_topics(identity: str) -> dict[str, str]:
    """topics/<slug>.md aggregating episodes + goals by keyword."""
    from zeline import memory as mem
    from zeline import goals as goals_mod

    # Gather texts
    docs: list[tuple[str, str, str]] = []  # (kind, ref, text)
    for ep in mem.list_episodes(identity, limit=200):
        if not isinstance(ep, dict):
            continue
        day = _fmt_date(ep.get("created_at", time.time()))
        text = ep.get("title", "") + " " + " ".join(ep.get("events", []))
        docs.append(("episode", f"[[daily/{day}]]", text))
    try:
        for g in goals_mod.list_goals(identity):
            if not isinstance(g, dict):
                continue
            docs.append(("goal", "[[goals]]", g.get("title", "") + " " + g.get("target", "")))
    except Exception:
        pass

    # Keyword -> docs
    topic_docs: dict[str, list[tuple[str, str]]] = {}
    for kind, ref, text in docs:
        for tok in mem._significant_tokens(text.lower()):
            if len(tok) < 3:
                continue
            topic_docs.setdefault(tok, []).append((kind, ref))

    files: dict[str, str] = {}
    # Keep topics with >= 2 mentions, cap at 50
    ranked = sorted(topic_docs.items(), key=lambda kv: -len(kv[1]))[:50]
    for tok, refs in ranked:
        if len(refs) < 2:
            continue
        slug = _slug(tok)
        lines = [f"# {tok}", "", f"*{len(refs)} mentions*", ""]
        kinds: dict[str, list[str]] = {}
        for kind, ref in refs:
            kinds.setdefault(kind, [])
            if ref not in kinds[kind]:
                kinds[kind].append(ref)
        for kind in sorted(kinds):
            lines.append(f"## {kind.capitalize()}s")
            for ref in kinds[kind][:30]:
                lines.append(f"- {ref}")
            lines.append("")
        # Related topics (shared docs heuristic: top co-occurring tokens)
        rel = _unique_rel(files, f"topics/{slug}.md")
        files[rel] = "\n".join(lines).rstrip() + "\n"
    return files


def _build_skills() -> dict[str, str]:
    """skills/<slug>.md — summary view of learned skills (source stays in learned_dir)."""
    from zeline import learning

    files: dict[str, str] = {}
    for sk in learning.list_learned_skills():
        name = _clean(sk.get("name", "untitled"), 200)
        desc = _clean(sk.get("description", ""), 500)
        slug = _slug(sk.get("file", name))
        lines = [
            f"# {name}",
            "",
            f"> {desc}" if desc else "> (no description)",
            "",
            "*This is a view. The skill source lives in Zeline's learned skills store.*",
            "",
            "## Related",
            "",
            "- [[README|Index]]",
        ]
        rel = _unique_rel(files, f"skills/{slug}.md")
        files[rel] = "\n".join(lines).rstrip() + "\n"
    return files


def _build_goals(identity: str) -> str:
    from zeline import goals as goals_mod

    lines = ["# Goals", ""]
    try:
        active = goals_mod.list_goals(identity, status="active")
        done = goals_mod.list_goals(identity, status="done")
    except Exception:
        active, done = [], []

    lines.append("## Active")
    lines.append("")
    if not active:
        lines.append("_No active goals._")
    for g in active:
        title = _clean(g.get("title", "Untitled"), 200)
        target = _clean(g.get("target", ""), 300)
        prog = g.get("progress", 0)
        dl = g.get("deadline") or "—"
        lines.append(f"### {title}")
        if target:
            lines.append(f"- Target: {target}")
        lines.append(f"- Progress: {prog}%")
        lines.append(f"- Deadline: {dl}")
        ms = g.get("milestones") or []
        if ms:
            lines.append("- Milestones:")
            for m in ms[:10]:
                mark = "x" if m.get("done") else " "
                lines.append(f"  - [{mark}] {_clean(m.get('title', ''), 200)}")
        lines.append("")
    lines.append("## Done")
    lines.append("")
    for g in done[:20]:
        lines.append(f"- [x] {_clean(g.get('title', 'Untitled'), 200)}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_user() -> str:
    from zeline import user_model

    lines = ["# User", ""]
    try:
        model = user_model.full_model()
    except Exception:
        model = {}
    traits = model.get("traits", {}) if isinstance(model, dict) else {}
    if not traits:
        lines.append("_No user model yet. Traits appear here as Zeline learns._")
        lines.append("")
        return "\n".join(lines)
    for dim in sorted(traits):
        keys = traits[dim] or {}
        lines.append(f"## {dim}")
        lines.append("")
        for key in sorted(keys):
            t = keys[key] or {}
            if t.get("confidence", 0) < 0.3:
                continue
            lines.append(f"- **{key}**: {_clean(t.get('value', ''), 300)} "
                         f"(confidence {t.get('confidence', 0)})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_index(identity: str, files: dict[str, str]) -> str:
    n_daily = sum(1 for f in files if f.startswith("daily/"))
    n_topics = sum(1 for f in files if f.startswith("topics/"))
    n_skills = sum(1 for f in files if f.startswith("skills/"))
    lines = [
        "# Memory Vault",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC from Zeline's memory stores. "
        "This is a read-friendly view — the source of truth stays in Zeline.*",
        "",
        "## Sections",
        "",
        "- [[user|User model]]",
        "- [[goals|Goals]]",
        f"- [[daily|Daily notes]] ({n_daily} days)",
        f"- [[topics|Topics]] ({n_topics} topics)",
        f"- [[skills|Skills]] ({n_skills} skills)",
        "",
        "## Daily notes",
        "",
    ]
    for f in sorted(files):
        if f.startswith("daily/"):
            day = f[len("daily/"):-len(".md")]
            lines.append(f"- [[{f[:-3]}|{day}]]")
    if n_topics:
        lines.append("")
        lines.append("## Topics")
        lines.append("")
        for f in sorted(files):
            if f.startswith("topics/"):
                name = f[len("topics/"):-len(".md")]
                lines.append(f"- [[{f[:-3]}|{name}]]")
    if n_skills:
        lines.append("")
        lines.append("## Skills")
        lines.append("")
        for f in sorted(files):
            if f.startswith("skills/"):
                name = f[len("skills/"):-len(".md")]
                lines.append(f"- [[{f[:-3]}|{name}]]")
    lines.append("")
    return "\n".join(lines)


def build_vault_files(identity: str = "cli:local") -> dict[str, str]:
    """Render the full vault as {relpath: markdown}. Pure view, no I/O."""
    files: dict[str, str] = {}
    files.update(_build_daily(identity))
    files.update(_build_topics(identity))
    files.update(_build_skills())
    files["goals.md"] = _build_goals(identity)
    files["user.md"] = _build_user()
    files["README.md"] = _build_index(identity, files)
    return files


# ---------------------------------------------------------------------------
# Export / sync
# ---------------------------------------------------------------------------

def _write_files(root: Path, files: dict[str, str]) -> int:
    n = 0
    for rel, content in files.items():
        p = root / rel
        # Safety: never escape vault root
        try:
            p.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
        except FileExistsError as exc:
            # A2-L7: jangan biarkan FileExistsError mentah bocor ke caller —
            # beri pesan yang menjelaskan file vault mana yang bermasalah.
            raise ValueError(f"vault write failed for {rel!r}: {exc}") from exc
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass  # best-effort hardening; content is already written
        n += 1
    return n


def export_vault(identity: str = "cli:local", path: str | Path | None = None) -> dict[str, Any]:
    """Full export: render and write every vault file. Returns stats."""
    root = Path(path).expanduser() if path else vault_path()
    files = build_vault_files(identity)
    n = _write_files(root, files)
    # Record hashes for future syncs
    state = _load_sync_state()
    state[identity] = {rel: _sha(content) for rel, content in files.items()}
    state[identity]["_version"] = VAULT_VERSION
    _save_sync_state(state)
    return {"ok": True, "path": str(root), "files": n,
            "daily": sum(1 for f in files if f.startswith("daily/")),
            "topics": sum(1 for f in files if f.startswith("topics/")),
            "skills": sum(1 for f in files if f.startswith("skills/"))}


def sync_vault(identity: str = "cli:local", path: str | Path | None = None) -> dict[str, Any]:
    """Incremental sync: only rewrite files whose content changed.

    Also removes vault files that no longer have a source (stale).
    """
    root = Path(path).expanduser() if path else vault_path()
    files = build_vault_files(identity)
    state = _load_sync_state()
    prev = state.get(identity, {})

    changed = 0
    for rel, content in files.items():
        if prev.get(rel) != _sha(content):
            p = root / rel
            try:
                p.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            changed += 1

    # Remove stale files (in state but not in current render), except README
    removed = 0
    for rel in list(prev):
        if rel.startswith("_"):
            continue
        if rel not in files and rel != "README.md":
            p = root / rel
            try:
                p.resolve().relative_to(root.resolve())
                if p.is_file():
                    p.unlink()
                    removed += 1
            except (ValueError, OSError):
                pass

    state[identity] = {rel: _sha(content) for rel, content in files.items()}
    state[identity]["_version"] = VAULT_VERSION
    _save_sync_state(state)
    return {"ok": True, "path": str(root), "changed": changed,
            "removed": removed, "total": len(files)}
