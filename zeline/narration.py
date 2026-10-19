"""Conservative, turn-local selection of pre-tool narration only.

Only complete, narrowly recognized mechanical announcements are suppressed.
Unknown language, decorated text and mixed messages deliberately pass through.
No tool metadata, configuration, I/O or model calls are involved.
"""

import re


# Closed grammar, not a keyword filter: no arbitrary object/complement wildcard.
_FILLER = r"(?:ok|okay|oke|baik)"
_ROUTINE = re.compile(
    rf"(?:{_FILLER}[, ]+)?(?:"
    r"(?:saya|aku|gua|gue) (?:akan |mau |coba )?"
    r"(?:cek(?: dulu)?|"
    r"(?:buka|klik|ketik|masukan|masukkan|baca) "
    r"(?:dulu )?(?:webnya|website|halamannya|file|filenya|tombolnya|pw|passwordnya))"
    r"|(?:i will|i'll) (?:open the (?:website|page|file)|"
    r"click the button|type the password|read the file)"
    rf"|{_FILLER})[.!]*",
    re.IGNORECASE,
)


class NarrationGate:
    """Select pre-tool narration; create a fresh instance for every agent turn.

    Never apply to history, final answers or streaming deltas. Retained output
    is byte-for-byte unchanged. Exact dedup suppresses only consecutive identical
    meaningful updates, retaining at most one string of up to 4096 characters.
    Oversized updates pass without retention and reset dedup; suppressed routine
    messages do not reset it. Grammar intentionally misses spelling
    variants, emoji prefixes and multiple same-line announcements rather than
    risk removing explanations, findings, questions or important plans.
    """

    def __init__(self) -> None:
        self._last: str | None = None

    def select(self, text: str) -> str | None:
        """Return narration unchanged, or None when it should not be emitted."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if all(_ROUTINE.fullmatch(line) for line in lines):
            return None
        # Keep one exact string, not a hash (no collision-induced lost findings).
        # Oversized meaningful updates still interrupt consecutive duplicates.
        if len(text) > 4096:
            self._last = None
            return text
        if text == self._last:
            return None
        self._last = text
        return text
