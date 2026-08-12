#!/usr/bin/env python3
"""
tools/guide_studio.py — Auto Guide Studio  (v4.2, sk35)

Ubah 1 airdrop jadi panduan step-by-step Bahasa Indonesia yang rapi + varian ringkas
buat Telegram/X, dengan referral link ke-embed otomatis & konsisten brand AirdropFinder.

Murni penyusun teks (offline, deterministik). Pengambilan screenshot/anotasi tiap step
didelegasi ke skill `browser`; publish didelegasi ke sk4/sk14. Zero-dep (dataclasses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GuideStep:
    action: str                      # apa yang dilakukan operator/pembaca
    url: str = ""                    # link tujuan (opsional)
    note: str = ""                   # tips/peringatan (opsional)
    screenshot_hint: str = ""        # petunjuk buat skill browser ambil SS


@dataclass
class GuideSpec:
    project: str
    chain: str
    steps: list                      # list[GuideStep]
    referral_url: str = ""
    difficulty: str = "mudah"        # mudah | sedang | sulit
    est_minutes: int = 10
    cost_note: str = "gas tipis"     # estimasi biaya
    deadline: str = ""               # mis. "TBA" / tanggal
    brand: str = "AirdropFinder"


def _ref(url: str, referral: str) -> str:
    """Embed referral kalau ada & belum ada di url."""
    if not referral:
        return url
    if not url:
        return referral
    return referral if referral.split("?")[0] in url else f"{url}"


def build_full_guide(spec: GuideSpec) -> str:
    """Panduan lengkap Markdown (ID), siap di-publish ke blog/Notion/web."""
    head = [
        f"# Panduan Airdrop {spec.project} ({spec.chain})",
        "",
        f"> 🏷️ *{spec.brand}* · Tingkat: *{spec.difficulty}* · Estimasi: *±{spec.est_minutes} menit* · Biaya: *{spec.cost_note}*"
        + (f" · Deadline: *{spec.deadline}*" if spec.deadline else ""),
        "",
        f"Airdrop *{spec.project}* lagi jalan di chain *{spec.chain}*. Ikutin langkah di bawah "
        "pelan-pelan, jangan skip. Selalu cek URL resmi sebelum connect wallet. 🔒",
        "",
        "## Yang disiapin dulu",
        "- Wallet (mis. MetaMask/Rabby) terisi gas secukupnya",
        f"- Jaringan *{spec.chain}* udah ke-add",
        "- Waktu santai biar nggak salah klik",
        "",
        "## Langkah-langkah",
    ]
    body = []
    for i, s in enumerate(spec.steps, 1):
        line = f"**{i}.** {s.action}"
        if s.url:
            line += f"\n   - 🔗 Link: {_ref(s.url, spec.referral_url)}"
        if s.note:
            line += f"\n   - 💡 {s.note}"
        if s.screenshot_hint:
            line += f"\n   - 🖼️ _[screenshot: {s.screenshot_hint}]_"
        body.append(line)

    tail = ["", "## Penutup",
            f"Selesai! Aktivitas kamu di *{spec.project}* udah ke-rekam on-chain. "
            "Konsisten interaksi sampai snapshot biar skor eligibility makin kuat (cek pakai sk31).",
            ""]
    if spec.referral_url:
        tail.append(f"🤝 Daftar lewat link ini biar saling untung: {spec.referral_url}")
        tail.append("")
    tail.append(f"_Disclaimer: airdrop = spekulatif, DYOR. Bukan jaminan cuan. — {spec.brand}_")
    return "\n".join(head + body + tail)


def build_short(spec: GuideSpec, platform: str = "telegram") -> str:
    """Varian ringkas buat Telegram (default) atau X/Twitter."""
    n = len(spec.steps)
    ref = f"\n\n🔗 {spec.referral_url}" if spec.referral_url else ""
    if platform == "x":
        txt = (f"🚨 Airdrop {spec.project} ({spec.chain}) lagi jalan!\n\n"
               f"⏱️ ±{spec.est_minutes} mnt · {spec.difficulty} · {spec.cost_note}\n"
               f"📋 {n} langkah gampang, panduan lengkap di bawah 👇{ref}\n\n"
               f"#Airdrop #{spec.project.replace(' ', '')} #{spec.brand}")
        return txt
    # telegram
    return (f"🪂 *{spec.project}* — {spec.chain}\n"
            f"⏱️ ±{spec.est_minutes} menit · tingkat {spec.difficulty} · {spec.cost_note}\n"
            f"📋 {n} langkah. Panduan lengkap + screenshot ada di postingan ⬇️{ref}\n"
            f"— {spec.brand}")


def build_bundle(spec: GuideSpec) -> dict:
    """Sekali jalan: full guide + varian TG + varian X."""
    return {
        "full_markdown": build_full_guide(spec),
        "telegram": build_short(spec, "telegram"),
        "x": build_short(spec, "x"),
        "screenshot_jobs": [s.screenshot_hint for s in spec.steps if s.screenshot_hint],
    }


if __name__ == "__main__":
    spec = GuideSpec(
        project="ZkProtoX", chain="Base",
        referral_url="https://zkprotox.xyz/?ref=airdropfinder",
        steps=[
            GuideStep("Buka situs resmi & connect wallet", url="https://zkprotox.xyz",
                      note="Pastikan domainnya bener!", screenshot_hint="halaman connect wallet"),
            GuideStep("Bridge 0.01 ETH ke Base", note="gas murah di Base"),
            GuideStep("Swap & sediakan likuiditas kecil", screenshot_hint="form swap"),
        ])
    b = build_bundle(spec)
    print(b["telegram"])
    print("\n--- screenshot jobs:", b["screenshot_jobs"])
