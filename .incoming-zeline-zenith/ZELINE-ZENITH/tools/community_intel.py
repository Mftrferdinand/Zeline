#!/usr/bin/env python3
"""
tools/community_intel.py — Community Intelligence  (v4.2, sk39)

"Dengerin" komunitas (Telegram/X/Discord) → topik trending, pertanyaan yang sering
muncul, sentimen, deteksi FUD, dan saran konten yang nyambung sama yang lagi ditanyain.

Murni analitik teks (offline, deterministik). Pengambilan pesan mentah didelegasi ke
sk4 (telegram) / sk6 / skill browser. `now` di-inject buat window trending. Zero-dep.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

POS_WORDS = {"bagus", "mantap", "gas", "untung", "cuan", "keren", "makasih", "thanks",
             "good", "love", "amazing", "profit", "moon", "wagmi", "legit"}
NEG_WORDS = {"scam", "rug", "jelek", "rugi", "lambat", "lemot", "error", "gagal", "bug",
             "bad", "hate", "loss", "ngefud", "fud", "ngga", "gak", "delay", "down"}
FUD_MARKERS = {"scam", "rug", "rugpull", "exit scam", "ngga dibayar", "gak cair",
               "penipuan", "tipu", "ga dibayar", "hilang"}
QUESTION_WORDS = {"gimana", "kapan", "berapa", "apakah", "kenapa", "dimana", "bisa",
                  "how", "when", "what", "why", "where", "is", "can", "?"}

STOPWORDS = {"yang", "di", "ke", "dari", "dan", "atau", "itu", "ini", "aku", "kamu",
             "saya", "gue", "lo", "udah", "sudah", "buat", "untuk", "dengan", "ada",
             "the", "a", "to", "of", "and", "is", "in", "it", "for", "on", "nya",
             "ya", "ga", "gak", "ngga", "aja", "kok", "sih", "dong", "min", "admin",
             "kak", "gan", "bang"}


@dataclass
class Message:
    text: str
    ts: float = 0.0
    reactions: int = 0


@dataclass
class IntelReport:
    total: int
    window_count: int
    top_topics: list                 # [(keyword, count)]
    trending_questions: list         # pertanyaan paling sering (teks)
    sentiment: dict                  # {pos, neg, neutral, score}
    fud_alerts: list                 # pesan yang ngandung marker FUD
    content_ideas: list

    def report(self) -> str:
        lines = [f"📥 {self.total} pesan ({self.window_count} dalam window) · "
                 f"sentimen {self.sentiment['label']} ({self.sentiment['score']:+.2f})"]
        if self.top_topics:
            lines.append("   🔥 topik: " + ", ".join(f"{k}({c})" for k, c in self.top_topics))
        if self.trending_questions:
            lines.append("   ❓ sering ditanya:")
            for q in self.trending_questions:
                lines.append(f"      - {q}")
        if self.fud_alerts:
            lines.append(f"   🚨 FUD: {len(self.fud_alerts)} pesan perlu ditangani")
        if self.content_ideas:
            lines.append("   💡 ide konten:")
            for c in self.content_ideas:
                lines.append(f"      - {c}")
        return "\n".join(lines)


def _tokens(text: str):
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in STOPWORDS and len(w) > 2]


def _is_question(text: str) -> bool:
    t = text.lower()
    if "?" in t:
        return True
    return any(t.startswith(q) or f" {q} " in f" {t} " for q in QUESTION_WORDS)


def _sentiment(messages: list) -> dict:
    pos = neg = 0
    for m in messages:
        toks = set(re.findall(r"[a-z]+", m.text.lower()))
        pos += len(toks & POS_WORDS)
        neg += len(toks & NEG_WORDS)
    total = pos + neg
    score = ((pos - neg) / total) if total else 0.0
    label = "positif" if score > 0.15 else "negatif" if score < -0.15 else "netral"
    return {"pos": pos, "neg": neg, "score": round(score, 3), "label": label}


def analyze(messages: list, now: float = 0.0, window_hours: float = 24,
            top_n: int = 8) -> IntelReport:
    """Analisis korpus pesan komunitas. `now`+window buat hitung 'trending'."""
    cutoff = now - window_hours * 3600 if now else 0.0
    window_msgs = [m for m in messages if (not now) or m.ts >= cutoff]

    # topik: frekuensi keyword di window (fallback ke semua kalau window kosong)
    src = window_msgs or messages
    counter = Counter()
    for m in src:
        counter.update(set(_tokens(m.text)))
    top_topics = counter.most_common(top_n)

    # pertanyaan trending: dedup kasar by normalized text, urut by frekuensi+reaksi
    q_counter = Counter()
    q_text = {}
    for m in src:
        if _is_question(m.text):
            key = " ".join(_tokens(m.text)[:6])
            if key:
                q_counter[key] += 1 + m.reactions
                q_text.setdefault(key, m.text.strip())
    trending_q = [q_text[k] for k, _ in q_counter.most_common(5)]

    sentiment = _sentiment(src)

    fud = [m.text.strip() for m in src
           if any(mk in m.text.lower() for mk in FUD_MARKERS)]

    # ide konten dari topik + pertanyaan
    ideas = []
    for k, c in top_topics[:3]:
        ideas.append(f"Konten edukasi soal '{k}' (lagi ramai, {c}x disebut)")
    if trending_q:
        ideas.append(f"FAQ/thread jawab: \"{trending_q[0]}\"")
    if fud:
        ideas.append("Klarifikasi resmi untuk meredam FUD yang beredar")

    return IntelReport(total=len(messages), window_count=len(window_msgs),
                       top_topics=top_topics, trending_questions=trending_q,
                       sentiment=sentiment, fud_alerts=fud, content_ideas=ideas)


if __name__ == "__main__":
    msgs = [
        Message("Kapan claim ZkProtoX dibuka min?", ts=100, reactions=5),
        Message("gas di base mahal ga sih buat farming", ts=200, reactions=2),
        Message("mantap cuan dari airdrop kemarin", ts=300),
        Message("ini scam ya? kok dananya gak cair", ts=400, reactions=8),
        Message("kapan claim nya min?", ts=500, reactions=3),
    ]
    print(analyze(msgs, now=1000, window_hours=24).report())
