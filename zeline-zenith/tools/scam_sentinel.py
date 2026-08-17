#!/usr/bin/env python3
"""
tools/scam_sentinel.py — Anti-Scam Sentinel  (v4.2, sk37)

Brand-protection defensif buat komunitas: deteksi domain/situs airdrop PALSU yang niru
project resmi (typosquat) + sinyal halaman berbahaya (minta seed phrase, tombol claim
drainer, SSL baru) → skor risiko + draft warning post siap broadcast.

READ-ONLY & defensif — gak nyerang, gak nge-exploit. Sejalan prinsip whitehat operator.
Pengambilan konten halaman/SSL didelegasi ke skill `browser`/sk6. Zero-dep (stdlib).
"""
from __future__ import annotations

from dataclasses import dataclass, field


def levenshtein(a: str, b: str) -> int:
    """Edit distance (stdlib, O(len(a)*len(b)))."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _root(domain: str) -> str:
    """Ambil nama inti domain (buang skema, www, dan TLD)."""
    d = domain.lower().strip()
    for pre in ("https://", "http://", "www."):
        if d.startswith(pre):
            d = d[len(pre):]
    d = d.split("/")[0]
    parts = d.split(".")
    return parts[0] if len(parts) >= 1 else d


@dataclass
class PageSignals:
    asks_seed_phrase: bool = False   # minta 12/24 kata — drainer klasik
    has_drainer_signature: bool = False  # pola setApprovalForAll/permit massal
    connect_wallet: bool = True
    ssl_age_days: float = 365.0      # umur sertifikat SSL (baru = mencurigakan)
    claim_button: bool = False
    external_redirect: bool = False  # redirect ke domain lain pas connect


@dataclass
class ScamVerdict:
    candidate: str
    official: str
    typosquat_distance: int
    risk_score: int                  # 0-100
    verdict: str                     # likely-safe | suspicious | likely-scam
    findings: list = field(default_factory=list)

    def report(self) -> str:
        lines = [f"🛡️ {self.candidate} vs {self.official}: {self.risk_score}/100 → {self.verdict.upper()}"]
        for f in self.findings:
            lines.append(f"   • {f}")
        return "\n".join(lines)


def analyze(candidate_domain: str, official_domain: str,
            signals: "PageSignals | None" = None) -> ScamVerdict:
    """Bandingkan domain kandidat vs resmi + sinyal halaman → verdict risiko."""
    signals = signals or PageSignals()
    cand_root = _root(candidate_domain)
    off_root = _root(official_domain)
    dist = levenshtein(cand_root, off_root)

    score = 0
    findings = []

    # Typosquat: mirip tapi nggak identik = bahaya tinggi
    if cand_root != off_root:
        if dist == 0:
            pass
        elif dist <= 2:
            score += 45
            findings.append(f"typosquat: '{cand_root}' beda {dist} huruf dari resmi '{off_root}'")
        elif off_root in cand_root or cand_root in off_root:
            score += 30
            findings.append(f"nama resmi nyangkut sebagai substring (impersonasi): '{cand_root}'")
        else:
            score += 5
            findings.append("nama domain beda jauh dari resmi (verifikasi manual)")

    # Sinyal halaman berbahaya (read-only)
    if signals.asks_seed_phrase:
        score += 40
        findings.append("🚩 minta SEED PHRASE — situs resmi TIDAK PERNAH minta ini")
    if signals.has_drainer_signature:
        score += 35
        findings.append("🚩 pola tanda-tangan drainer (approval massal)")
    if signals.external_redirect:
        score += 15
        findings.append("redirect ke domain lain saat connect wallet")
    if signals.ssl_age_days < 14:
        score += 12
        findings.append(f"SSL baru ({signals.ssl_age_days:.0f} hari) — situs sangat baru")
    if signals.claim_button and signals.ssl_age_days < 30 and cand_root != off_root:
        score += 8
        findings.append("tombol claim di domain baru non-resmi")

    score = max(0, min(100, score))
    if score >= 60:
        verdict = "likely-scam"
    elif score >= 25:
        verdict = "suspicious"
    else:
        verdict = "likely-safe"

    if not findings:
        findings.append("tidak ada sinyal mencurigakan terdeteksi (tetap DYOR)")

    return ScamVerdict(candidate=candidate_domain, official=official_domain,
                       typosquat_distance=dist, risk_score=score,
                       verdict=verdict, findings=findings)


def warning_post(v: ScamVerdict, project: str, brand: str = "AirdropFinder") -> str:
    """Draft warning siap broadcast ke channel (kalau verdict bukan likely-safe)."""
    if v.verdict == "likely-safe":
        return ""
    emoji = "🚨🚨" if v.verdict == "likely-scam" else "⚠️"
    head = "AWAS SCAM" if v.verdict == "likely-scam" else "HATI-HATI"
    bullets = "\n".join(f"• {f}" for f in v.findings)
    return (f"{emoji} *{head}: {project}* {emoji}\n\n"
            f"Domain `{v.candidate}` terdeteksi *{v.verdict}* (risiko {v.risk_score}/100).\n\n"
            f"{bullets}\n\n"
            f"✅ Selalu pakai domain RESMI: `{v.official}`\n"
            f"❌ JANGAN masukkan seed phrase di mana pun.\n\n"
            f"Stay safe, guys. — {brand}")


if __name__ == "__main__":
    v = analyze("zkprot0x.xyz", "zkprotox.xyz",
                PageSignals(asks_seed_phrase=True, ssl_age_days=3, claim_button=True))
    print(v.report())
    print()
    print(warning_post(v, "ZkProtoX"))
