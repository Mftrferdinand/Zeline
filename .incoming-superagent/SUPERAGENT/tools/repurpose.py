#!/usr/bin/env python3
"""
tools/repurpose.py — Omni-Repurpose Engine  (v4.2, sk40)

1 sumber konten → banyak format sekaligus, di-lokalisasi ID: X thread, post Telegram,
carousel IG, script TikTok/Reels, script YouTube. Sekali jalan, konsisten brand.

Murni penyusun teks (offline, deterministik) dengan batasan per-platform (panjang
karakter, jumlah slide). Pelengkap sk27 (kalender/strategi) — sk40 fokus transformasi
format. Publish didelegasi ke sk4/sk14. Zero-dep.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Batasan kasar tiap platform
X_LIMIT = 280


@dataclass
class SourceContent:
    title: str
    key_points: list                 # poin-poin inti (list[str])
    cta: str = ""                    # call to action
    referral_url: str = ""
    hashtags: list = field(default_factory=list)
    brand: str = "AirdropFinder"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def to_x_thread(src: SourceContent) -> list:
    """X/Twitter thread: tweet 1 hook, lalu 1 poin/tweet, ditutup CTA. <=280 char."""
    tags = " ".join(f"#{h.lstrip('#')}" for h in src.hashtags)
    thread = [_clip(f"🧵 {src.title}\n\n(thread 👇)", X_LIMIT)]
    n = len(src.key_points)
    for i, p in enumerate(src.key_points, 1):
        thread.append(_clip(f"{i}/{n} {p}", X_LIMIT))
    closing = src.cta or "Follow buat update airdrop berikutnya."
    if src.referral_url:
        closing += f"\n🔗 {src.referral_url}"
    if tags:
        closing += f"\n\n{tags}"
    thread.append(_clip(closing, X_LIMIT))
    return thread


def to_telegram(src: SourceContent) -> str:
    """Post Telegram: ringkas, bullet, link di akhir."""
    bullets = "\n".join(f"• {p}" for p in src.key_points)
    out = f"📢 *{src.title}*\n\n{bullets}"
    if src.cta:
        out += f"\n\n👉 {src.cta}"
    if src.referral_url:
        out += f"\n🔗 {src.referral_url}"
    out += f"\n\n— {src.brand}"
    return out


def to_ig_carousel(src: SourceContent) -> list:
    """Slide carousel IG: slide 1 cover, tiap poin 1 slide, slide akhir CTA."""
    slides = [{"slide": 1, "type": "cover", "text": src.title}]
    for i, p in enumerate(src.key_points, 2):
        slides.append({"slide": i, "type": "point", "text": _clip(p, 150)})
    slides.append({"slide": len(slides) + 1, "type": "cta",
                   "text": (src.cta or "Save & share!") +
                           (f"\n{src.referral_url}" if src.referral_url else "")})
    return slides


def to_tiktok_script(src: SourceContent) -> dict:
    """Script TikTok/Reels pendek: hook 3 detik, body cepat, CTA."""
    body = [f"{i}. {p}" for i, p in enumerate(src.key_points, 1)]
    return {
        "hook": f"Jangan lewatin {src.title.lower()}! 👀",
        "body": body,
        "cta": src.cta or "Follow biar gak ketinggalan airdrop!",
        "on_screen": [src.title] + [p[:40] for p in src.key_points],
        "est_seconds": 15 + 7 * len(src.key_points),
    }


def to_youtube_script(src: SourceContent) -> dict:
    """Script YouTube lebih panjang: intro, segmen per poin, outro."""
    segments = [{"heading": p, "talking_points":
                 [f"Jelasin kenapa '{p}' penting", "Kasih contoh konkret", "Tips praktis"]}
                for p in src.key_points]
    return {
        "title": src.title,
        "intro": f"Halo guys, di video ini kita bahas {src.title}. Simak sampai habis ya.",
        "segments": segments,
        "outro": (src.cta or "Like, subscribe, dan nyalain lonceng.") +
                 (f" Link di deskripsi: {src.referral_url}" if src.referral_url else ""),
        "est_minutes": max(3, 2 * len(src.key_points)),
    }


def repurpose_all(src: SourceContent) -> dict:
    """Semua format sekaligus."""
    return {
        "x_thread": to_x_thread(src),
        "telegram": to_telegram(src),
        "ig_carousel": to_ig_carousel(src),
        "tiktok": to_tiktok_script(src),
        "youtube": to_youtube_script(src),
    }


if __name__ == "__main__":
    src = SourceContent(
        title="3 Airdrop Base Paling Worth Difarming",
        key_points=["ZkProtoX — points program aktif",
                    "BaseSwap — volume tinggi, gas murah",
                    "Aerodrome — likuiditas gede"],
        cta="Cek panduan lengkap di channel.",
        referral_url="https://airdropfinder.id",
        hashtags=["airdrop", "base", "crypto"])
    out = repurpose_all(src)
    print(out["x_thread"][0])
    print("slides:", len(out["ig_carousel"]), "· tiktok est:", out["tiktok"]["est_seconds"], "s")
