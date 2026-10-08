"""Dialectic user modeling.

Builds a deepening model of the user across sessions. Unlike static USER.md,
traits here carry confidence scores, supporting evidence, and update history —
the model evolves dialectically as new interactions provide evidence.

Dimensions:
- communication: style, language, formality, verbosity preference
- technical: skill level, preferred tools, domains of expertise
- goals: active objectives, priorities
- preferences: likes, dislikes, working style
- constraints: time, budget, things to avoid
- relationships: key people (references, not duplicates of people files)
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _model_path() -> Path:
    p = Path.home() / ".zeline" / "user_model.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    try:
        return json.loads(_model_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"traits": {}, "updated_at": None}


def _save(model: dict) -> None:
    model["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _model_path().write_text(
        json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    _model_path().chmod(0o600)


def get_trait(dimension: str, key: str) -> dict | None:
    """Get a single trait. Returns {value, confidence, evidence, updated} or None."""
    model = _load()
    return model["traits"].get(dimension, {}).get(key)


def set_trait(dimension: str, key: str, value: str, confidence: float,
              evidence: str = "") -> dict:
    """Set/update a trait with confidence (0.0-1.0) and evidence.

    If the trait exists, confidence is blended: new evidence reinforces or
    revises. Returns the updated trait dict.
    """
    # Clamp confidence to valid range
    confidence = max(0.0, min(1.0, float(confidence)))
    model = _load()
    traits = model.setdefault("traits", {})
    dim = traits.setdefault(dimension, {})
    existing = dim.get(key)
    now = time.strftime("%Y-%m-%d")
    if existing:
        # Dialectic blend: weighted average favoring newer evidence slightly
        old_conf = existing.get("confidence", 0.5)
        blended = round(old_conf * 0.4 + confidence * 0.6, 2)
        history = existing.get("history", [])
        history.append({
            "value": existing["value"],
            "confidence": old_conf,
            "at": existing.get("updated", now),
        })
        # Keep last 5
        history = history[-5:]
    else:
        blended = round(confidence, 2)
        history = []
    dim[key] = {
        "value": value,
        "confidence": blended,
        "evidence": evidence,
        "updated": now,
        "history": history,
    }
    _save(model)
    return dim[key]


def get_dimension(dimension: str) -> dict:
    """Get all traits in a dimension."""
    return _load()["traits"].get(dimension, {})


def full_model() -> dict:
    """Return the entire user model."""
    return _load()


def summarize() -> str:
    """Human-readable summary of the user model for prompts."""
    model = _load()
    traits = model.get("traits", {})
    if not traits:
        return "No user model yet."
    lines = []
    for dim, keys in sorted(traits.items()):
        lines.append(f"## {dim}")
        for key, t in sorted(keys.items()):
            conf = t.get("confidence", 0)
            # Only show traits with decent confidence
            if conf >= 0.3:
                lines.append(f"- {key}: {t['value']} (confidence {conf})")
    return "\n".join(lines) if lines else "No confident traits yet."


# Valid dimensions (agent should stick to these)
DIMENSIONS = frozenset({
    "communication", "technical", "goals",
    "preferences", "constraints", "relationships",
})
