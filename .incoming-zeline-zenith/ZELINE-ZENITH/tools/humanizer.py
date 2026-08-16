"""
tools/humanizer.py — Humanizer & Brand Voice  (v4.1)

Deteksi "AI-tell" dan rewrite deterministik jadi suara natural + adaptasi brand voice.
Keyless, stdlib (regex). Rewrite "berat" tetap lewat LLM (model_registry) — di sini
cuma yang aman diotomasi.

JUJUR: ini buat bikin tulisanmu kedengeran kayak kamu (marketing/konten/email/brand),
BUKAN buat ngecoh detektor akademik atau klaim karya orang/AI sebagai manusia di
konteks yang melarang.

Pakai:
    from humanizer import score, humanize, to_brand
    print(score(text))                    # {ai_score, tells, suggestions}
    print(humanize(text))                 # rewrite aman
    print(to_brand(text, voice={...}))    # buang kata terlarang + tweak ringan
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (pola, label, saran) — heuristik AI-tell
TELLS = [
    (r"\bit'?s (important|worth) (to note|noting|mentioning)\b", "hedging-klise", "hapus, langsung ke poin"),
    (r"\b(moreover|furthermore|additionally|in conclusion|in summary)\b", "transisi-kaku", "hapus / ganti titik"),
    (r"\b(delve|leverage|robust|seamless|utilize|holistic|synergy)\b", "jargon-korporat", "pakai kata sehari-hari"),
    (r"\bnot only\b.*\bbut also\b", "konstruksi-AI", "pecah jadi 2 kalimat"),
    (r"\b(semoga membantu|hope this helps|happy to help)\b", "penutup-kosong", "potong total"),
    (r"\bsebagai (sebuah )?(AI|asisten|model)\b", "AI-disclaimer", "hapus"),
    (r"\b(tapestry|realm|landscape|ever-evolving|game-changer)\b", "klise-AI", "konkretkan"),
]
# tricolon "a, b, and c" (sinyal, bukan selalu salah)
TRICOLON = re.compile(r"\b\w+,\s+\w+,\s+and\s+\w+\b", re.I)


@dataclass
class ScoreResult:
    ai_score: int
    tells: list
    suggestions: list

    def __repr__(self):
        return f"ai_score={self.ai_score} tells={self.tells} suggestions={self.suggestions}"


def score(text: str) -> ScoreResult:
    tells, suggestions = [], []
    for pat, label, fix in TELLS:
        if re.search(pat, text, re.I):
            tells.append(label)
            suggestions.append(f"{label} → {fix}")
    if len(TRICOLON.findall(text)) >= 2:
        tells.append("tricolon-berulang")
        suggestions.append("tricolon-berulang → variasikan, jangan selalu 3")
    # burstiness: kalau panjang kalimat terlalu seragam → tanda AI
    sents = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sents) >= 4:
        lens = [len(s.split()) for s in sents]
        mean = sum(lens) / len(lens)
        var = sum((x - mean) ** 2 for x in lens) / len(lens)
        if var < 6:                       # ritme terlalu rata
            tells.append("ritme-seragam")
            suggestions.append("ritme-seragam → selang-seling kalimat panjang & pendek")
    ai_score = min(100, len(tells) * 18)
    return ScoreResult(ai_score, tells, suggestions)


def humanize(text: str) -> str:
    """Rewrite deterministik: buang tell yang AMAN diotomasi (hapus filler)."""
    out = text
    # buang frasa hedging/penutup/disclaimer di awal/tengah
    kill = [
        r"\bIt'?s important to note that\s*", r"\bIt'?s worth noting that\s*",
        r"\b(Moreover|Furthermore|Additionally),\s*", r"\bIn (conclusion|summary),\s*",
        r"\b(Semoga membantu|Hope this helps)[.!]?\s*", r"\bSebagai (sebuah )?(AI|asisten|model)[^.,]*[.,]\s*",
    ]
    for pat in kill:
        out = re.sub(pat, "", out, flags=re.I)
    # "not only X but also Y" → "X, juga Y"
    out = re.sub(r"not only (.+?) but also (.+?)([.!?])", r"\1, juga \2\3", out, flags=re.I)
    # rapikan spasi ganda & kapital awal kalimat yang kepotong
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out


def to_brand(text: str, voice: dict) -> str:
    """Adaptasi ringan ke brand voice. `voice` = {forbid:[...], replace:{a:b}, ...}.

    Rewrite berat (restrukturisasi nada penuh) → pakai LLM dgn `voice` sbg system prompt.
    Di sini cuma enforce kata terlarang + substitusi.
    """
    out = humanize(text)
    for word in voice.get("forbid", []):
        out = re.sub(rf"\b{re.escape(word)}\b", "", out, flags=re.I)
    for a, b in voice.get("replace", {}).items():
        out = re.sub(rf"\b{re.escape(a)}\b", b, out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


if __name__ == "__main__":
    sample = ("It's important to note that our platform is robust, seamless, and scalable. "
              "Moreover, we leverage cutting-edge technology. Hope this helps!")
    print(score(sample))
    print("---")
    print(humanize(sample))
    print("---")
    print(to_brand(sample, voice={"forbid": ["robust", "leverage"], "replace": {"platform": "tool"}}))
