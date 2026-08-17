#!/usr/bin/env python3
"""
tools/sybil_audit.py — Sybil-Resistance Self-Audit  (v4.2, sk31)

Agent ini multi-wallet airdrop runner. SEBELUM eksekusi, audit POLA SENDIRI biar
gak kebaca sybil filter (yang bisa nge-disqualify SEMUA wallet sekaligus). Tool ini
ngambil aktivitas sekumpulan wallet (yang KITA kontrol) dan nge-flag korelasi yang
gampang ke-cluster: funding satu sumber, timing seragam, gas identik, jumlah tx kembar,
nonce sinkron.

Ini jaring keselamatan operator (nyelametin duit & effort) — BUKAN buat ngecoh proyek
orang lain di luar wallet sendiri. Output = risiko + saran de-correlation konkret.

Zero-dep (statistics/dataclasses). Input = list[WalletActivity] yang KITA punya.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WalletActivity:
    address: str
    funded_by: Optional[str] = None        # alamat sumber dana pertama
    first_tx_ts: Optional[float] = None     # epoch detik
    tx_count: int = 0
    gas_price_gwei: Optional[float] = None  # gas tipikal
    interacted_contracts: tuple = ()        # set kontrak yang disentuh


@dataclass
class SybilFinding:
    risk: str                               # low | medium | high
    score: int                              # 0-100 (makin tinggi makin berisiko ke-cluster)
    signals: list                           # penjelasan tiap sinyal yang nyala
    advice: list                            # de-correlation konkret


def _shared_funding(wallets: list) -> float:
    sources = [w.funded_by for w in wallets if w.funded_by]
    if not sources:
        return 0.0
    top = max(set(sources), key=sources.count)
    return sources.count(top) / len(wallets)


def _timing_cluster(wallets: list, window_s: float = 3600) -> float:
    ts = sorted(w.first_tx_ts for w in wallets if w.first_tx_ts is not None)
    if len(ts) < 2:
        return 0.0
    # fraksi wallet yang first-tx-nya dalam window yang sama dengan tetangganya
    clustered = sum(1 for i in range(1, len(ts)) if ts[i] - ts[i - 1] <= window_s)
    return clustered / (len(ts) - 1)


def _uniformity(values: list) -> float:
    """0 = beragam, 1 = identik. Pakai coefficient of variation terbalik."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return 0.0
    mean = statistics.mean(vals)
    if mean == 0:
        return 1.0 if all(v == 0 for v in vals) else 0.0
    cv = statistics.pstdev(vals) / abs(mean)
    return max(0.0, 1.0 - min(1.0, cv * 3))   # cv kecil → uniform → mendekati 1


def _contract_overlap(wallets: list) -> float:
    sets = [set(w.interacted_contracts) for w in wallets if w.interacted_contracts]
    if len(sets) < 2:
        return 0.0
    inter = set.intersection(*sets)
    union = set.union(*sets)
    return len(inter) / len(union) if union else 0.0


def audit(wallets: list) -> SybilFinding:
    """Audit korelasi antar-wallet milik sendiri. Return risiko + saran."""
    if len(wallets) < 2:
        return SybilFinding("low", 0, ["<2 wallet — tidak ada pola cluster"], [])

    signals = []
    advice = []
    weighted = 0.0

    f = _shared_funding(wallets)
    if f >= 0.5:
        weighted += f * 30
        signals.append(f"funding seragam: {f*100:.0f}% wallet didanai dari sumber yang sama")
        advice.append("variasikan sumber funding (CEX berbeda / fresh wallet relay / waktu beda)")

    t = _timing_cluster(wallets)
    if t >= 0.5:
        weighted += t * 25
        signals.append(f"timing berdempet: {t*100:.0f}% first-tx dalam window yang sama")
        advice.append("acak jadwal first-tx (jitter hari/jam), jangan batch sekaligus")

    g = _uniformity([w.gas_price_gwei for w in wallets])
    if g >= 0.7:
        weighted += g * 15
        signals.append(f"gas price hampir identik (uniformity {g:.2f})")
        advice.append("biarkan gas mengikuti kondisi natural tiap waktu, jangan hardcode sama")

    c = _uniformity([float(w.tx_count) for w in wallets])
    if c >= 0.8:
        weighted += c * 10
        signals.append(f"jumlah tx kembar antar-wallet (uniformity {c:.2f})")
        advice.append("variasikan jumlah & jenis interaksi per wallet")

    o = _contract_overlap(wallets)
    if o >= 0.8:
        weighted += o * 20
        signals.append(f"overlap kontrak tinggi: {o*100:.0f}% kontrak identik di semua wallet")
        advice.append("kasih tiap wallet jejak unik (protokol/urutan berbeda)")

    score = int(round(min(100.0, weighted)))
    risk = "high" if score >= 60 else "medium" if score >= 30 else "low"
    if not signals:
        signals.append("tidak ada korelasi kuat terdeteksi — pola sudah cukup ter-disperse")
    return SybilFinding(risk=risk, score=score, signals=signals, advice=advice)


if __name__ == "__main__":
    ws = [
        WalletActivity("0x1", funded_by="0xCEX", first_tx_ts=1000, tx_count=10,
                       gas_price_gwei=20, interacted_contracts=("0xa", "0xb")),
        WalletActivity("0x2", funded_by="0xCEX", first_tx_ts=1100, tx_count=10,
                       gas_price_gwei=20, interacted_contracts=("0xa", "0xb")),
        WalletActivity("0x3", funded_by="0xCEX", first_tx_ts=1200, tx_count=11,
                       gas_price_gwei=21, interacted_contracts=("0xa", "0xb")),
    ]
    r = audit(ws)
    print(f"risk={r.risk} score={r.score}")
    for s in r.signals:
        print("  signal:", s)
    for a in r.advice:
        print("  fix:", a)
