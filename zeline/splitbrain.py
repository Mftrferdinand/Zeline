"""Split-brain architecture (OpenHuman parity).

Two layers:
- Reflex: fast triage agent, responds in seconds to simple queries.
  Uses a small model or limited tools. No deep reasoning.
- Reasoning core: full agent loop for complex multi-step tasks.

The router decides which layer handles each incoming message.
"""

from __future__ import annotations

import re


# Patterns indicating a simple/reflex query
_REFLEX_PATTERNS = [
    re.compile(r"^(hai|halo|hello|hi|hey)\b", re.I),
    re.compile(r"^(makasih|thanks|thank you|ok|oke|siap)\b", re.I),
    re.compile(r"^(jam berapa|what time)", re.I),
    re.compile(r"^(tanggal berapa|what.*date)", re.I),
    re.compile(r"^(siapa namamu|what.*your name)", re.I),
    re.compile(r"^(help|bantuan|/help)$", re.I),
]

# Patterns indicating complex/reasoning query
_COMPLEX_PATTERNS = [
    re.compile(r"\b(buatkan|buatin|create|build|implement)\b", re.I),
    re.compile(r"\b(analisa|analyze|research|teliti)\b", re.I),
    re.compile(r"\b(cari|search|find).{10,}", re.I),  # long search queries
    re.compile(r"\?", re.I),  # questions often need reasoning
]


def classify(text: str) -> str:
    """Classify a message as 'reflex' (fast) or 'reasoning' (deep).

    Returns 'reflex' or 'reasoning'.
    """
    text = (text or "").strip()
    if not text:
        return "reflex"
    # Short messages are usually reflex
    if len(text) < 20:
        for pat in _REFLEX_PATTERNS:
            if pat.search(text):
                return "reflex"
        # Very short + no complex markers = reflex
        if len(text) < 10:
            return "reflex"
    # Check complex patterns
    for pat in _COMPLEX_PATTERNS:
        if pat.search(text):
            return "reasoning"
    # Long messages need reasoning
    if len(text) > 200:
        return "reasoning"
    # Default: reflex for short, reasoning for medium+
    return "reflex" if len(text) < 50 else "reasoning"


def reflex_response(text: str | None, user_name: str = "") -> str | None:
    """Generate a fast reflex response without LLM call.

    Returns response string, or None if reflex can't handle it
    (fall through to reasoning).
    """
    t = (text or "").strip().lower()
    # Greetings
    if re.match(r"^(hai|halo|hello|hi|hey)\b", t):
        name = f" {user_name}" if user_name else ""
        return f"Hai{name}! Ada yang bisa dibantu?"
    # Thanks
    if re.match(r"^(makasih|thanks|thank you)\b", t):
        return "Sama-sama! 👍"
    # OK acknowledgments
    if re.match(r"^(ok|oke|siap|baik)\.?$", t):
        return "👍"
    # Time
    if re.match(r"^(jam berapa|what time)", t):
        import time
        return f"Sekarang {time.strftime('%H:%M')} WIB."
    # Date
    if re.match(r"^(tanggal berapa|what.*date today)", t):
        import time
        return f"Hari ini {time.strftime('%A, %d %B %Y')}."
    return None  # fall through to reasoning
