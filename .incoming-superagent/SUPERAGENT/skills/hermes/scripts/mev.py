"""
hermes/mev.py — MEV Protection / Private Transaction Submission  (v4.0)

Masalah: swap & snipe yang di-broadcast ke PUBLIC mempool empuk buat
sandwich/frontrun. Modul ini ngirim signed tx lewat PRIVATE relay (off-ramp
dari public mempool) biar searcher gak bisa lihat tx in-flight.

Relay yang didukung (EVM):
- flashbots  → https://rpc.flashbots.net/fast   (gratis, public good, MEV+gas refund)
- mevblocker → https://rpc.mevblocker.io         (CoW/Agnostic; back-run rebate ke user)
- custom     → set HERMES_PRIVATE_RPC ke endpoint pilihan (mis. bloXroute, builder sendiri)

CATATAN PENTING (jangan over-promise ke operator):
- Private path NGURANGIN exposure, BUKAN jaminan. Tx bisa uncled / fallback ke
  public, dan gagal landing kalau nonce gak konsisten atau wallet resend publik.
- Flashbots Protect "no failed tx": tx cuma masuk block kalau gak revert →
  hemat gas dari tx gagal, tapi bisa bikin tx "stuck pending" kalau kondisi berubah.
- Private RPC TIDAK melindungi dari risiko token-level (honeypot, tax, malicious
  approval). Itu tetap tugas safety gate (honeypot.is/GoPlus) di swap.md/sniping.md.

Verified live (2026): rpc.flashbots.net/fast pakai eth_sendRawTransaction;
MEV Blocker per Jan 2026 di bawah Consensys Special Mechanisms Group.
Saat implement, cek docs.flashbots.net / mevblocker.io buat chain & param terbaru.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Literal

import httpx
from web3 import Web3

Relay = Literal["flashbots", "mevblocker", "custom", "public"]

# Endpoint per relay. Chain non-mainnet: cek docs masing-masing relay.
PRIVATE_RPC = {
    "flashbots":  "https://rpc.flashbots.net/fast",   # Ethereum mainnet
    "mevblocker": "https://rpc.mevblocker.io",          # Ethereum mainnet
}

# Chain yang punya jalur private umum. Selain ini → fallback public + WARNING.
PRIVATE_SUPPORTED_CHAINS = {1}   # Ethereum mainnet paling matang; perluas sesuai relay


@dataclass
class SendResult:
    status: str                 # "sent_private" | "sent_public" | "error"
    tx_hash: Optional[str] = None
    relay: Optional[str] = None
    warning: Optional[str] = None
    error: Optional[str] = None


def resolve_relay(chain_id: int,
                  prefer: Relay = "flashbots") -> tuple[Optional[str], Optional[str]]:
    """Return (rpc_url, warning). rpc_url None = jatuh ke public mempool."""
    custom = os.environ.get("HERMES_PRIVATE_RPC")
    if prefer == "custom" or (custom and prefer not in PRIVATE_RPC):
        if custom:
            return custom, None
        return None, "HERMES_PRIVATE_RPC belum di-set → fallback public mempool"

    if prefer == "public":
        return None, "mode public dipilih eksplisit — TANPA proteksi MEV"

    if chain_id not in PRIVATE_SUPPORTED_CHAINS and not custom:
        return None, (f"chain {chain_id} belum punya private relay terdaftar → "
                      f"fallback public mempool (rawan sandwich). Set HERMES_PRIVATE_RPC "
                      f"kalau relay-nya support chain ini.")
    url = PRIVATE_RPC.get(prefer) or custom
    if not url:
        return None, f"relay '{prefer}' gak punya endpoint terdaftar → fallback public"
    return url, None


def send_private_tx(signed_raw: bytes | str, chain_id: int,
                    prefer: Relay = "flashbots",
                    public_w3: Optional[Web3] = None) -> SendResult:
    """
    Kirim signed raw tx lewat private relay. Kalau relay gak tersedia untuk chain
    ini, fallback ke public_w3 (kalau dikasih) dengan WARNING — JANGAN diam-diam.
    signed_raw: hasil account.sign_transaction(tx).raw_transaction (bytes / hex).
    """
    raw_hex = signed_raw if isinstance(signed_raw, str) else "0x" + signed_raw.hex()
    rpc_url, warn = resolve_relay(chain_id, prefer)

    if rpc_url is None:
        if public_w3 is None:
            return SendResult("error", error=warn or "tidak ada relay & tidak ada fallback public")
        h = public_w3.eth.send_raw_transaction(raw_hex).hex()
        return SendResult("sent_public", tx_hash=h, relay="public", warning=warn)

    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_sendRawTransaction", "params": [raw_hex],
            })
            data = r.json()
        if "error" in data:
            # private gagal → eskalasi, jangan diam-diam broadcast public
            return SendResult("error", relay=prefer, error=str(data["error"]),
                              warning="private relay nolak; JANGAN auto-resend ke public "
                                      "(bisa kena frontrun). Cek error dulu.")
        return SendResult("sent_private", tx_hash=data["result"], relay=prefer, warning=warn)
    except Exception as e:
        return SendResult("error", relay=prefer, error=repr(e))


def protect_provider(chain_id: int, prefer: Relay = "flashbots") -> Optional[Web3]:
    """w3 yang nge-broadcast lewat private relay. None kalau gak ada relay."""
    rpc_url, _ = resolve_relay(chain_id, prefer)
    return Web3(Web3.HTTPProvider(rpc_url)) if rpc_url else None


# Solana: gak ada "private mempool" kayak EVM. Proteksi MEV via Jito bundle
# (tip account + sendBundle) atau prioritization fee tinggi. Lihat swap.md §Solana.
JITO_BLOCK_ENGINE = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"


if __name__ == "__main__":
    url, warn = resolve_relay(1, "flashbots")
    print("Ethereum private rpc:", url, "| warn:", warn)
    url2, warn2 = resolve_relay(8453, "flashbots")
    print("Base private rpc:", url2, "| warn:", warn2)
