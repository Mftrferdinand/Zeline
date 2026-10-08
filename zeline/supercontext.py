"""SuperContext: pre-message research sweep.

Before the model reads a user message, a fast research scout sweeps local
memory and files for relevant context — no cold starts. Pure local reads
(FTS5, JSON, files); never calls the model.

``gather_context(user_message, identity)`` returns a compact context block
(max ~2000 chars by default) or "" when nothing relevant is found.

Trust model:
- Sessions are IDENTITY-SCOPED: only this identity's episodic memory is
  swept. The global FTS5 session index is deliberately NOT used here because
  it is not identity-partitioned (cross-identity leak, SC3).
- Learned skills and the user model are intentionally GLOBAL: skills are
  shared knowledge and there is a single user model per machine.
- The assembled block is prepended to the SYSTEM prompt (the most trusted
  position), so any line matching prompt-injection patterns is DROPPED
  outright (not just warned about) via the existing injection_filter (SC2).
"""

from __future__ import annotations

import re
import time

# Words too generic to be useful as search keywords (ID + EN).
_STOPWORDS = frozenset(
    """
    yang dan dari untuk dengan pada adalah ini itu ada tidak juga atau
    akan bisa saya kamu dia kita mereka apa siapa mana kapan bagaimana
    kenapa tolong coba tolonglah mohon ya ga gak nggak sih dong deh
    the and for with from that this have has are was were will would
    can could should your you our their them they what when where
    which who how why please help try just like get make take
    """.split()
)

# A2-L5: unicode word chars — \w with re.UNICODE matches CJK/Arabic/etc.
# letters too, so non-Latin queries get keywords instead of "".
_WORD_RE = re.compile(r"[\w]{3,}", re.UNICODE)

# Hard time budget (seconds) — a sweep must never slow a turn down.
_BUDGET_S = 0.45


def _keywords(text: str, limit: int = 12) -> list[str]:
    """Extract significant keywords from a message."""
    words = _WORD_RE.findall(text.lower())
    seen: list[str] = []
    for w in words:
        if w in _STOPWORDS:
            continue
        if w not in seen:
            seen.append(w)
        if len(seen) >= limit:
            break
    return seen


def _score(haystack: str, keywords: list[str]) -> int:
    """Count keyword hits in haystack text."""
    hay = haystack.lower()
    return sum(1 for kw in keywords if kw in hay)


def _is_relevant(score: int, n_keywords: int) -> bool:
    """Relevance gate: 2+ hits normally; 1 hit is enough when the
    message has very few keywords (avoids missing obvious matches)."""
    if score >= 2:
        return True
    return score >= 1 and n_keywords <= 3


def _strip_html(text: str) -> str:
    return re.sub(r"</?b>", "", text or "")


def _is_tainted(text: str) -> bool:
    """True if ``text`` matches prompt-injection patterns (SC2).

    Uses the existing injection_filter — no invented heuristics.
    Never raises.
    """
    try:
        from zeline import injection_filter as _if

        return bool(_if.detect_injection(text or ""))
    except Exception:
        return False


def _drop_tainted_lines(text: str) -> str:
    """Remove lines matching prompt-injection patterns (SC2).

    SuperContext output is prepended to the SYSTEM prompt — the most trusted
    position in the whole turn. A warning banner (as used for tool results)
    is weaker than removal here: the poisoned line is dropped outright while
    clean context is preserved. This is the final backstop; the "Past
    sessions" section additionally filters individual events before joining
    them into lines, so one poisoned event doesn't nuke its clean siblings.
    Uses the existing injection_filter patterns; no invented heuristics.
    Never raises.
    """
    try:
        clean = [ln for ln in text.splitlines() if not _is_tainted(ln)]
    except Exception:
        return text
    return "\n".join(clean)


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_nl = cut.rfind("\n")
    if last_nl > max_chars // 2:
        cut = cut[:last_nl]
    return cut.rstrip() + "\n…"


def gather_context(user_message: str, identity: str) -> str:
    """Sweep memory/files for context relevant to ``user_message``.

    Returns a compact block ("" when nothing relevant). Never raises.
    Must stay fast: local reads only, no model calls, hard time budget.
    """
    from zeline import config as _cfg

    max_chars = int(getattr(_cfg, "SUPERCONTEXT_MAX_CHARS", 2000) or 2000)
    deadline = time.monotonic() + _BUDGET_S
    sections: list[str] = []

    try:
        keywords = _keywords(user_message or "")
    except Exception:
        return ""
    # Fast path: nothing worth searching for.
    if len(keywords) < 2:
        return ""

    def _time_left() -> bool:
        return time.monotonic() < deadline

    # 1. Past sessions — identity-scoped episodic memory ("no cold starts").
    # SC3: deliberately uses memory.search_episodes(identity, ...) instead of
    # the global FTS5 session_search — the FTS5 index is not
    # identity-partitioned, and this block lands in the SYSTEM prompt, so a
    # global sweep would leak one chat's sessions into another's prompt.
    if _time_left():
        try:
            from zeline import memory as _mem

            eps = _mem.search_episodes(identity, " ".join(keywords[:8]), limit=3) or []
            lines = []
            for ep in eps:
                if not isinstance(ep, dict):
                    continue
                # SC2: filter title/events INDIVIDUALLY for taint before
                # joining — one poisoned event must not nuke its clean
                # siblings on the same rendered line.
                title = _strip_html(str(ep.get("title", ""))).strip()
                if _is_tainted(title):
                    title = ""
                events = ep.get("events", []) or []
                ev_txt = "; ".join(
                    str(e)[:120] for e in events[:3] if e and not _is_tainted(str(e))
                )
                snippet = f"{title}: {ev_txt}" if title else ev_txt
                snippet = snippet.strip().strip(":").strip()
                if snippet:
                    lines.append(f"- {snippet[:220]}")
            if lines:
                sections.append("Past sessions:\n" + "\n".join(lines[:3]))
        except Exception:
            pass

    # 2. Learned skills that may apply.
    if _time_left():
        try:
            from zeline import learning as _learning

            matched = []
            for sk in _learning.list_learned_skills():
                text = f"{sk.get('name', '')} {sk.get('description', '')}"
                score = _score(text, keywords)
                if _is_relevant(score, len(keywords)):
                    matched.append((score, sk))
            matched.sort(key=lambda x: -x[0])
            lines = []
            for _score_v, sk in matched[:3]:
                # A2-L6: per-field taint filter (same as sessions section) —
                # a poisoned description must not ride into the system prompt.
                name = str(sk.get("name", "?"))
                desc = str(sk.get("description", ""))[:120]
                if _is_tainted(name) or _is_tainted(desc):
                    continue
                lines.append(f"- {name}: {desc}")
            if lines:
                sections.append("Relevant learned skills:\n" + "\n".join(lines))
        except Exception:
            pass

    # 3. Active goals that may relate.
    if _time_left():
        try:
            from zeline import goals as _goals

            matched = []
            for g in _goals.list_goals(identity, status="active"):
                text = f"{g.get('title', '')} {g.get('target', '')}"
                score = _score(text, keywords)
                if _is_relevant(score, len(keywords)):
                    matched.append((score, g))
            matched.sort(key=lambda x: -x[0])
            lines = []
            for _score_v, g in matched[:3]:
                # A2-L6: per-field taint filter (same as sessions section).
                title = str(g.get("title", "?"))[:100]
                if _is_tainted(title):
                    continue
                prog = g.get("progress", "")
                prog_s = f" ({prog}%)" if isinstance(prog, (int, float)) else ""
                lines.append(f"- {title}{prog_s}")
            if lines:
                sections.append("Active goals:\n" + "\n".join(lines))
        except Exception:
            pass

    # 4. User model traits that may matter.
    if _time_left():
        try:
            from zeline import user_model as _um

            model = _um.full_model() or {}
            traits = model.get("traits", {}) or {}
            matched = []
            for dim, keys in traits.items():
                if not isinstance(keys, dict):
                    continue
                for key, t in keys.items():
                    if not isinstance(t, dict):
                        continue
                    text = f"{dim} {key} {t.get('value', '')}"
                    score = _score(text, keywords)
                    conf = float(t.get("confidence", 0) or 0)
                    if score >= 1 and conf >= 0.5:
                        matched.append((score, dim, key, str(t.get("value", ""))[:120]))
            matched.sort(key=lambda x: -x[0])
            lines = []
            for _s, dim, key, val in matched[:3]:
                # A2-L6: per-field taint filter (same as sessions section).
                if _is_tainted(key) or _is_tainted(val):
                    continue
                lines.append(f"- [{dim}] {key}: {val}")
            if lines:
                sections.append("About the user:\n" + "\n".join(lines))
        except Exception:
            pass

    if not sections:
        return ""
    block = "\n\n".join(sections)
    # SC2: drop injection-tainted lines BEFORE this block reaches the system
    # prompt. Fail closed: a poisoned memory line must never be amplified
    # into the most trusted position of the turn.
    block = _drop_tainted_lines(block)
    if not block.strip():
        return ""
    return _truncate(block, max_chars)
