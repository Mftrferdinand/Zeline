#!/usr/bin/env python3
"""
tools/contract_watch.py — Contract-Change & Claim-Address Watcher  (v4.2, sk38)

Pantau kontrak protokol yang lagi difarming → deteksi perubahan berbahaya antar
snapshot: proxy implementation di-upgrade, claim address berubah, admin/owner ganti,
code hash beda, fungsi sensitif baru muncul. Lindungin user terutama pas momen claim.

READ-ONLY & defensif. Pengambilan snapshot on-chain (impl/admin/code hash/ABI)
didelegasi ke H9 contract_read + sk10. Tool ini cuma DIFF dua snapshot. Zero-dep.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Fungsi yang kalau tiba-tiba muncul = sinyal bahaya tinggi
SENSITIVE_FUNCS = {
    "setclaimaddress", "setmerkleroot", "withdrawall", "drain", "selfdestruct",
    "setowner", "transferownership", "upgradeto", "setimplementation",
    "pause", "blacklist", "setfee", "mint",
}


@dataclass
class ContractSnapshot:
    address: str
    impl_address: str = ""           # untuk proxy: alamat implementation
    claim_address: str = ""          # alamat tujuan claim (kalau ada)
    admin: str = ""                  # admin/owner
    code_hash: str = ""              # keccak code (atau hash apa pun yang konsisten)
    functions: list = field(default_factory=list)  # daftar nama fungsi (lowercase ok)


@dataclass
class WatchAlert:
    severity: str                    # info | warning | critical
    kind: str
    message: str


@dataclass
class WatchResult:
    address: str
    alerts: list = field(default_factory=list)

    @property
    def max_severity(self) -> str:
        order = {"info": 0, "warning": 1, "critical": 2}
        return max((a.severity for a in self.alerts), key=lambda s: order[s], default="info")

    @property
    def changed(self) -> bool:
        return bool(self.alerts)

    def report(self) -> str:
        if not self.alerts:
            return f"✅ {self.address}: tidak ada perubahan terdeteksi"
        icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
        lines = [f"{icon[self.max_severity]} {self.address}: {len(self.alerts)} perubahan ({self.max_severity})"]
        for a in self.alerts:
            lines.append(f"   {icon[a.severity]} [{a.kind}] {a.message}")
        return "\n".join(lines)


def _norm_funcs(funcs):
    return {f.lower() for f in funcs}


def diff(prev: ContractSnapshot, curr: ContractSnapshot) -> WatchResult:
    """Bandingkan dua snapshot kontrak → daftar alert berbobot severity."""
    alerts = []

    if prev.claim_address and curr.claim_address and prev.claim_address != curr.claim_address:
        alerts.append(WatchAlert("critical", "claim_address_changed",
                                  f"claim address berubah {prev.claim_address} → {curr.claim_address} "
                                  f"— JANGAN claim sampai diverifikasi resmi"))

    if prev.impl_address and curr.impl_address and prev.impl_address != curr.impl_address:
        alerts.append(WatchAlert("critical", "proxy_upgraded",
                                  f"implementation proxy di-upgrade {prev.impl_address} → {curr.impl_address}"))

    if prev.admin and curr.admin and prev.admin != curr.admin:
        alerts.append(WatchAlert("warning", "admin_changed",
                                  f"admin/owner berubah {prev.admin} → {curr.admin}"))

    if prev.code_hash and curr.code_hash and prev.code_hash != curr.code_hash:
        sev = "critical" if not (prev.impl_address and curr.impl_address) else "warning"
        alerts.append(WatchAlert(sev, "code_changed",
                                  "bytecode kontrak berubah (code hash beda)"))

    new_funcs = _norm_funcs(curr.functions) - _norm_funcs(prev.functions)
    sens_new = sorted(new_funcs & SENSITIVE_FUNCS)
    if sens_new:
        alerts.append(WatchAlert("critical", "sensitive_function_added",
                                  f"fungsi sensitif baru muncul: {', '.join(sens_new)}"))
    benign_new = sorted(new_funcs - SENSITIVE_FUNCS)
    if benign_new:
        alerts.append(WatchAlert("info", "function_added",
                                  f"fungsi baru (non-sensitif): {', '.join(benign_new)}"))

    return WatchResult(address=curr.address, alerts=alerts)


def safe_to_claim(prev: ContractSnapshot, curr: ContractSnapshot) -> bool:
    """Shortcut: aman claim kalau tidak ada alert critical."""
    return diff(prev, curr).max_severity != "critical"


if __name__ == "__main__":
    p = ContractSnapshot("0xAbc", impl_address="0x111", claim_address="0xCLAIM1",
                         admin="0xAdmin", code_hash="h1", functions=["claim", "balanceof"])
    c = ContractSnapshot("0xAbc", impl_address="0x222", claim_address="0xEVIL",
                         admin="0xAdmin", code_hash="h2", functions=["claim", "balanceof", "setClaimAddress"])
    print(diff(p, c).report())
    print("safe to claim?", safe_to_claim(p, c))
