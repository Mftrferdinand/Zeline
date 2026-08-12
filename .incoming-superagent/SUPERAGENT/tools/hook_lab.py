#!/usr/bin/env python3
"""
tools/hook_lab.py — Hook A/B Lab + Performance Predictor  (v4.2, sk42)

Generate banyak varian hook (judul/opening) + skor prediksi "stop-scroll" berbasis
heuristik pola engagement (angka, curiosity gap, emosi, pertanyaan, power words,
panjang ideal). Pilih top-N. Pelengkap sk28/sk29 — sk42 fokus HOOK & prediksi.

Murni heuristik (offline, deterministik). Skor = pendukung keputusan, bukan jaminan
viral. Zero-dep (stdlib).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CURIOSITY = {"rahasia", "ternyata", "jarang", "gak nyangka", "ini alasan", "kenapa",
             "secret", "nobody", "begini", "trik", "cara", "hack"}
EMOTION = {"gila", "ngeri", "wajib", "bahaya", "awas", "cuan", "rugi", "gratis",
             "fomo", "parah", "insane", "shocking", "jangan"}
POWER = {"terbukti", "terbaik", "tercepat", "instan", "mudah", "step", "panduan",
         "ultimate", "proven", "worth", "legit", "real"}
URGENCY = {"sekarang", "hari ini", "deadline", "terakhir", "buruan", "segera",
           "limited", "now", "sebelum", "habis"}


@dataclass
class HookScore:
    text: str
    score: int                       # 0-100
    signals: list = field(default_factory=list)

    def report(self) -> str:
        return f"[{self.score:>3}] {self.text}" + (f"  ({', '.join(self.signals)})" if self.signals else "")


def _has_number(t: str) -> bool:
    return bool(re.search(r"\d", t))


def _wordset(t: str):
    return set(re.findall(r"[a-z]+", t.lower()))


def score_hook(text: str) -> HookScore:
    """Skor satu hook 0-100 dari sinyal stop-scroll. Deterministik."""
    t = text.strip()
    words = _wordset(t)
    n_words = len(re.findall(r"\S+", t))
    score = 30  # baseline
    signals = []

    if _has_number(t):
        score += 14
        signals.append("angka")
    if words & CURIOSITY:
        score += 16
        signals.append("curiosity")
    if words & EMOTION:
        score += 14
        signals.append("emosi")
    if words & POWER:
        score += 10
        signals.append("power-word")
    if words & URGENCY:
        score += 10
        signals.append("urgency")
    if "?" in t:
        score += 8
        signals.append("pertanyaan")
    if t and (t[0].isupper() or t[0] in "🚨🔥👀⚠️"):
        score += 2

    # panjang ideal hook: 4-12 kata. Penalti kalau terlalu pendek/panjang.
    if 4 <= n_words <= 12:
        score += 8
        signals.append("panjang-ideal")
    elif n_words > 18:
        score -= 12
        signals.append("kepanjangan")
    elif n_words < 3:
        score -= 10
        signals.append("kependekan")

    # ALL CAPS berlebihan = spammy
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        score -= 8
        signals.append("caps-berlebih")

    score = max(0, min(100, score))
    return HookScore(text=t, score=score, signals=signals)


def rank_hooks(hooks: list, top: int = 3) -> list:
    """Skor semua hook, urut desc, kembalikan semua (top dipakai pemanggil)."""
    scored = sorted((score_hook(h) for h in hooks), key=lambda h: h.score, reverse=True)
    return scored


def generate_hooks(topic: str, n_points: int = 0) -> list:
    """Bikin set varian hook dari sebuah topik pakai template terbukti."""
    topic = topic.strip().rstrip(".")
    num = n_points if n_points > 0 else 3
    templates = [
        f"{num} {topic} yang wajib kamu tau sekarang",
        f"Ternyata begini cara {topic.lower()} yang jarang dibahas",
        f"Jangan {topic.lower()} sebelum nonton ini 👀",
        f"Rahasia {topic.lower()} yang bikin cuan 🔥",
        f"Kenapa {topic.lower()}? Ini alasannya",
        f"Panduan {topic} tercepat (step by step)",
        f"Awas! {topic} bisa bahaya kalau salah langkah ⚠️",
        f"{topic}: gratis, gampang, worth banget",
    ]
    return templates


if __name__ == "__main__":
    topic = "Airdrop Base"
    hooks = generate_hooks(topic, n_points=3)
    for h in rank_hooks(hooks)[:3]:
        print(h.report())
