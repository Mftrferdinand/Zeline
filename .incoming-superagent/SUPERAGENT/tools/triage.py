"""
tools/triage.py — Inbox / Notification Triage  (v4.0)

Ambil pesan masuk dari channel yang lo sambungin (Telegram/Discord/dll), skor
prioritas, kelompokin, dan sodorin yang penting aja. Hemat waktu tiap hari.

Core heuristik = keyless (urgency keyword + VIP sender + mention). Ringkasan
opsional bisa pakai LLM (sk7) lewat summary_fn — tapi prioritisasi jalan tanpa itu.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

DEFAULT_URGENT = {"urgent", "asap", "now", "penting", "darurat", "down", "error", "gagal",
                  "hack", "drained", "rug", "liquidat", "expire", "deadline", "claim"}


@dataclass
class Message:
    source: str            # "telegram" | "discord" | ...
    sender: str
    text: str
    ts: float = field(default_factory=time.time)


@dataclass
class Triaged:
    message: Message
    score: float
    reasons: list[str]


def score_message(m: Message, vips: set[str], urgent_kw: set[str],
                  my_handles: set[str]) -> Triaged:
    score, reasons = 0.0, []
    low = m.text.lower()

    if m.sender.lower() in {v.lower() for v in vips}:
        score += 5; reasons.append("dari VIP")
    hits = [k for k in urgent_kw if k in low]
    if hits:
        score += 2 * len(hits); reasons.append(f"kata urgent: {', '.join(hits[:3])}")
    if any(h.lower() in low for h in my_handles):
        score += 3; reasons.append("nge-mention lo")
    if "?" in m.text:
        score += 1; reasons.append("ada pertanyaan")
    age_h = (time.time() - m.ts) / 3600
    if age_h < 1:
        score += 1; reasons.append("baru")
    # link/address → mungkin actionable
    if re.search(r"0x[0-9a-fA-F]{40}|https?://", m.text):
        score += 1; reasons.append("ada link/address")
    return Triaged(m, score, reasons)


def triage(messages: list[Message],
           vips: Optional[set[str]] = None,
           urgent_kw: Optional[set[str]] = None,
           my_handles: Optional[set[str]] = None,
           top_k: int = 10,
           summary_fn: Optional[Callable[[list[Message]], str]] = None) -> dict:
    vips = vips or set()
    urgent_kw = urgent_kw or DEFAULT_URGENT
    my_handles = my_handles or set()

    scored = [score_message(m, vips, urgent_kw, my_handles) for m in messages]
    scored.sort(key=lambda x: x.score, reverse=True)
    high = [t for t in scored if t.score >= 5]
    rest = [t for t in scored if t.score < 5][:top_k]

    digest = {
        "total": len(messages),
        "high_priority": [{"from": t.message.sender, "text": t.message.text[:120],
                           "score": t.score, "why": t.reasons} for t in high],
        "other": [{"from": t.message.sender, "text": t.message.text[:80]} for t in rest],
    }
    if summary_fn and messages:
        try:
            digest["summary"] = summary_fn(messages)
        except Exception:
            pass
    return digest


def format_digest(digest: dict) -> str:
    out = [f"📥 Triage: {digest['total']} pesan"]
    if digest.get("summary"):
        out += ["", f"📋 {digest['summary']}"]
    if digest["high_priority"]:
        out += ["", "🔴 *Prioritas tinggi:*"]
        for h in digest["high_priority"]:
            out.append(f"• {h['from']}: {h['text']}  _({', '.join(h['why'])})_")
    if digest["other"]:
        out += ["", f"⚪ Lainnya ({len(digest['other'])}): " +
                ", ".join(o["from"] for o in digest["other"][:8])]
    return "\n".join(out)


if __name__ == "__main__":
    msgs = [
        Message("telegram", "bos", "URGENT: server down, cek dong @hermes", ts=time.time()),
        Message("discord", "randomguy", "gm semua", ts=time.time() - 7200),
        Message("telegram", "partner", "claim window LayerZero expire 2 jam lagi!", ts=time.time()),
        Message("discord", "spammer", "join my group", ts=time.time() - 100),
    ]
    d = triage(msgs, vips={"bos", "partner"}, my_handles={"@hermes"})
    print(format_digest(d))
