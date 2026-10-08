"""GEPA-like automatic learning from successful tool patterns.

Unlike manual learn_skill, this runs AUTOMATICALLY:
1. Tracks tool call sequences during agent execution
2. After complex tasks, analyzes for repeatable patterns
3. Generates skill proposals without being asked
4. Verifies before promoting to permanent (anti "confidently wrong")

Failure mode addressed: a skill starts as DRAFT. It's only promoted to
permanent after N successful uses. Bad skills can be deprecated.

This is the "genuinely novel" cross-session improvement that leading agents have
and Zeline lacked — now closed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path


def _gepa_dir() -> Path:
    d = Path.home() / ".zeline" / "gepa"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sequences_path() -> Path:
    return _gepa_dir() / "sequences.jsonl"


def _drafts_path() -> Path:
    return _gepa_dir() / "drafts.json"


def record_tool_call(tool_name: str, success: bool) -> None:
    """Record a tool call for pattern analysis. Called by agent loop."""
    try:
        entry = {
            "tool": tool_name,
            "success": success,
            "ts": time.time(),
        }
        with open(_sequences_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _load_sequences(last_n: int = 200) -> list[dict]:
    try:
        lines = _sequences_path().read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines[-last_n:] if l.strip()]
    except Exception:
        return []


def extract_patterns(min_length: int = 3, min_occurrences: int = 2) -> list[dict]:
    """Find repeated tool sequences that succeeded.

    Returns list of {sequence: [tools], occurrences: int, success_rate: float}.
    """
    seqs = _load_sequences()
    if len(seqs) < min_length * min_occurrences:
        return []
    # Find n-grams of successful tool calls
    patterns: Counter = Counter()
    successes: Counter = Counter()
    for n in range(min_length, min_length + 3):
        for i in range(len(seqs) - n + 1):
            window = seqs[i:i + n]
            key = tuple(w["tool"] for w in window)
            patterns[key] += 1
            if all(w["success"] for w in window):
                successes[key] += 1
    result = []
    for key, count in patterns.most_common(20):
        if count >= min_occurrences:
            sr = successes[key] / count
            # Only propose patterns with high success rate
            if sr >= 0.8:
                result.append({
                    "sequence": list(key),
                    "occurrences": count,
                    "success_rate": round(sr, 2),
                })
    # Deduplicate: remove patterns that are subsequences of longer patterns
    # (prevents draft explosion from overlapping n-grams)
    filtered = []
    for i, p in enumerate(result):
        seq = tuple(p["sequence"])
        is_subseq = False
        for j, q in enumerate(result):
            if i == j:
                continue
            qseq = tuple(q["sequence"])
            if len(qseq) > len(seq):
                # Check if seq is contiguous subsequence of qseq
                for k in range(len(qseq) - len(seq) + 1):
                    if qseq[k:k+len(seq)] == seq:
                        is_subseq = True
                        break
            if is_subseq:
                break
        if not is_subseq:
            filtered.append(p)
    return filtered


def propose_skill(pattern: dict) -> dict:
    """Generate a skill draft from a detected pattern."""
    seq = pattern["sequence"]
    name = f"auto-{'-'.join(seq[:3])}"
    # Sanitize
    name = "".join(c if c.isalnum() or c == "-" else "-" for c in name)[:40]
    draft = {
        "id": f"draft_{hashlib.md5(json.dumps(seq).encode()).hexdigest()[:8]}",
        "name": name,
        "description": f"Auto-learned: {' → '.join(seq)}",
        "sequence": seq,
        "occurrences": pattern["occurrences"],
        "success_rate": pattern["success_rate"],
        "status": "draft",  # draft -> testing -> permanent (or deprecated)
        "uses": 0,
        "successful_uses": 0,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "content": (
            f"# {name}\n\n"
            f"> Auto-learned pattern: {' → '.join(seq)}\n\n"
            f"This skill was automatically distilled from {pattern['occurrences']} "
            f"observed executions with {pattern['success_rate']:.0%} success rate.\n\n"
            f"## Steps\n\n"
            + "\n".join(f"{i+1}. Use `{t}`" for i, t in enumerate(seq))
            + "\n"
        ),
    }
    return draft


def _load_drafts() -> dict:
    try:
        return json.loads(_drafts_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_drafts(drafts: dict) -> None:
    _drafts_path().write_text(json.dumps(drafts, indent=2), encoding="utf-8")
    _drafts_path().chmod(0o600)


def auto_learn() -> list[str]:
    """Run automatic pattern detection and draft generation.

    Called after complex tasks. Returns list of new draft IDs created.
    """
    patterns = extract_patterns()
    if not patterns:
        return []
    drafts = _load_drafts()
    new_ids = []
    for p in patterns:
        draft = propose_skill(p)
        if draft["id"] not in drafts:
            drafts[draft["id"]] = draft
            new_ids.append(draft["id"])
    if new_ids:
        _save_drafts(drafts)
    return new_ids


def record_skill_use(draft_id: str, success: bool) -> str:
    """Record a skill use. Promotes to permanent after 3 successful uses.

    Returns the new status.
    """
    drafts = _load_drafts()
    d = drafts.get(draft_id)
    if not d:
        return "not_found"
    d["uses"] += 1
    if success:
        d["successful_uses"] += 1
    # Promotion: 3+ uses with 100% success, or 5+ uses with 80%+ success
    if d["status"] == "draft":
        if d["successful_uses"] >= 3 and d["uses"] == d["successful_uses"]:
            d["status"] = "permanent"
            # Promote to actual skill file
            _promote_to_skill(d)
        elif d["uses"] >= 5:
            sr = d["successful_uses"] / d["uses"]
            if sr >= 0.8:
                d["status"] = "permanent"
                _promote_to_skill(d)
            else:
                d["status"] = "deprecated"  # confidently wrong -> kill it
    elif d["status"] == "permanent" and d["uses"] >= 10:
        # Ongoing monitoring: deprecate if success drops
        sr = d["successful_uses"] / d["uses"]
        if sr < 0.5:
            d["status"] = "deprecated"
    _save_drafts(drafts)
    return d["status"]


def _promote_to_skill(draft: dict) -> None:
    """Write a permanent skill file from a validated draft."""
    from zeline import learning
    learning.save_learned_skill(
        draft["name"],
        draft["description"],
        draft["content"] + f"\n\n*Auto-promoted after {draft['successful_uses']} successful uses.*\n",
    )


def get_drafts(status: str | None = None) -> list[dict]:
    """List skill drafts, optionally filtered by status."""
    drafts = _load_drafts()
    result = list(drafts.values())
    if status:
        result = [d for d in result if d["status"] == status]
    return sorted(result, key=lambda d: d.get("created", ""), reverse=True)
