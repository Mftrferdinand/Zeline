# Web3 Connect & Sign-in

Reference untuk integrasi Hermes ke dApp: Sign-In with Ethereum (SIWE), Sign-In with Solana (SIWS), WalletConnect v2, EIP-712 typed data signing, dan EIP-1271 (smart contract wallet) verification.

## Sign-In with Ethereum (SIWE — EIP-4361)

Standar login Web3 yang dipakai 90% dApp sekarang (OpenSea, Snapshot, Lens, dst).

### Flow
```
dApp                          Hermes (wallet)
 │                                  │
 ├─► GET /siwe/nonce ──────────────►│
 │                                  │
 │◄──── nonce ──────────────────────┤
 │                                  │
 │   compose SIWE message           │
 │   user accept → sign EIP-191     │
 │                                  │
 ├─► POST /siwe/verify ────────────►│
 │   {message, signature}           │
 │                                  │
 │   server verifies, set session   │
```

### Sign SIWE Message (Python)

```python
from eth_account import Account
from eth_account.messages import encode_defunct
from datetime import datetime, timezone

def build_siwe_message(domain: str, address: str, statement: str, uri: str,
                       chain_id: int, nonce: str, version: str = "1") -> str:
    """Kompose SIWE message sesuai EIP-4361 standard."""
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        f"{statement}\n\n"
        f"URI: {uri}\n"
        f"Version: {version}\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )

def sign_siwe(account, message: str) -> str:
    """Sign personal_sign style (EIP-191)."""
    msg = encode_defunct(text=message)
    signed = account.sign_message(msg)
    return signed.signature.hex()

# Hermes flow:
# 1. GET nonce dari dApp
# 2. SHOW SIWE message ke user, minta konfirmasi (sangat penting!)
# 3. Sign + POST kembali
```

### Verifikasi SIWE (sisi server, kalau Hermes jadi authenticator)

```python
from eth_account.messages import encode_defunct
from eth_account import Account
import re

def verify_siwe(message: str, signature: str, expected_address: str,
                 expected_domain: str, expected_nonce: str) -> tuple[bool, str]:
    # Recover address
    recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    if recovered.lower() != expected_address.lower():
        return False, "address mismatch"

    # Validate fields
    lines = message.split("\n")
    if not lines[0].startswith(expected_domain):
        return False, "domain mismatch"
    if f"Nonce: {expected_nonce}" not in message:
        return False, "nonce mismatch"

    # Validate timestamp (issued_at + expires_at kalau ada)
    m = re.search(r"Issued At: (\S+)", message)
    # parse ISO8601 → cek < now dan, kalau ada Expires At, > now
    return True, "ok"
```

## Sign-In with Solana (SIWS)

Tidak ada standar resmi, tapi pattern umum: sign message yang berisi domain + nonce + timestamp.

```python
from solders.keypair import Keypair
from nacl.signing import SigningKey

def sign_siws(keypair: Keypair, domain: str, nonce: str) -> tuple[str, bytes]:
    issued_at = datetime.now(timezone.utc).isoformat()
    message = (f"{domain} wants you to sign in.\n\n"
               f"Address: {keypair.pubkey()}\n"
               f"Nonce: {nonce}\n"
               f"Issued At: {issued_at}")
    signature = keypair.sign_message(message.encode()).to_bytes()
    return message, signature
```

## EIP-712 Typed Data Signing

Banyak dApp (Seaport/OpenSea, Permit2, GMX, Uniswap permit, gasless meta-tx) butuh sign EIP-712, bukan personal_sign.

```python
from eth_account.messages import encode_typed_data
from eth_account import Account

def sign_typed_data(account, full_msg: dict) -> str:
    """full_msg shape:
    {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, ...],
            "Permit": [{"name": "owner", "type": "address"}, ...]
        },
        "primaryType": "Permit",
        "domain": {"name": "USD Coin", "version": "2", "chainId": 1,
                   "verifyingContract": "0x..."},
        "message": {"owner": "0x...", "spender": "0x...", ...}
    }
    """
    encoded = encode_typed_data(full_message=full_msg)
    return account.sign_message(encoded).signature.hex()
```

### Permit (ERC-2612) — gasless approval

Banyak token modern (USDC, DAI, sebagian token baru) support `permit()` — user sign tipped data, siapapun bisa relay approval tanpa user bayar gas approval terpisah.

```python
async def build_permit_signature(w3, account, token_addr: str, spender: str,
                                   value: int, deadline: int) -> dict:
    token = w3.eth.contract(address=token_addr, abi=ERC20_PERMIT_ABI)
    nonce = token.functions.nonces(account.address).call()
    name = token.functions.name().call()
    version = token.functions.version().call() if hasattr(token.functions, "version") else "1"

    domain = {"name": name, "version": version,
              "chainId": w3.eth.chain_id, "verifyingContract": token_addr}
    types = {"Permit": [
        {"name": "owner", "type": "address"},
        {"name": "spender", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ]}
    message = {"owner": account.address, "spender": spender,
               "value": value, "nonce": nonce, "deadline": deadline}
    sig = sign_typed_data(account, {"types": types, "domain": domain,
                                     "primaryType": "Permit", "message": message})
    # split signature jadi v, r, s
    sig_bytes = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig)
    r, s, v = sig_bytes[:32], sig_bytes[32:64], sig_bytes[64]
    return {"v": v, "r": r.hex(), "s": s.hex(), "deadline": deadline, "value": value}
```

## WalletConnect v2

WalletConnect = bridge antara dApp (browser) dan wallet (mobile/agent). Hermes bertindak sebagai **wallet** (yang menerima request).

### Python SDK

Tidak ada SDK Python resmi yang produksi-grade. Opsi:

1. **`walletconnect-py`** (community): basic v2 support
2. **Manual relay**: connect ke `wss://relay.walletconnect.com` dengan project ID, decode SymKey-encrypted JSON-RPC requests
3. **Bridge via Node.js subprocess**: pakai `@walletconnect/sign-client` resmi, expose ke Python lewat HTTP/IPC

Rekomendasi: opsi 3 untuk produksi.

### Pattern Manual (sketch)

```python
"""Sketch — production code lihat referensi resmi WalletConnect.

URI format: wc:{topic}@2?relay-protocol=irn&symKey={hex}

Flow:
1. User paste URI dari dApp (atau scan QR)
2. Parse topic + symKey
3. WebSocket connect ke relay
4. Subscribe topic, decrypt incoming session_propose
5. Show ke user (apa yg di-request, namespace, chain mana)
6. User accept → kirim session_settle
7. Loop: terima session_request (eth_sendTransaction, personal_sign, etc.)
   → tampilkan ke user → sign/eksekusi → kirim session_response
"""

import json
from urllib.parse import urlparse, parse_qs
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

def parse_wc_uri(uri: str) -> dict:
    if not uri.startswith("wc:"):
        raise ValueError("not a WalletConnect URI")
    rest = uri[3:]
    topic, query = rest.split("@", 1)
    version, params = query.split("?", 1)
    qs = parse_qs(params)
    return {
        "topic": topic,
        "version": version,
        "relay_protocol": qs["relay-protocol"][0],
        "sym_key": bytes.fromhex(qs["symKey"][0]),
    }

def decrypt_wc_message(sym_key: bytes, encrypted_b64: str) -> dict:
    import base64
    raw = base64.b64decode(encrypted_b64)
    # WC v2: type(1) || iv(12) || sealed(ciphertext+tag)
    msg_type = raw[0]
    nonce = raw[1:13]
    ciphertext = raw[13:]
    plain = ChaCha20Poly1305(sym_key).decrypt(nonce, ciphertext, None)
    return json.loads(plain)
```

### Keamanan WalletConnect (PENTING)

WalletConnect adalah vector phishing #1. Sebelum sign request dari session WC, Hermes wajib:

1. **Tampilkan metadata dApp** ke user — name, url, icons. Cek apakah URL match nama yang diklaim.
2. **Decode tx data** — jangan sign blind. Pakai 4byte directory + decoded params.
3. **Simulate tx** sebelum sign (Tenderly API atau `eth_call`).
4. **Block known scam contracts** — maintain blocklist (atau pakai service seperti Blockaid, GoPlus).

```python
async def screen_tx_request(w3, tx: dict, dapp_url: str) -> dict:
    warnings = []

    # Decode function
    if tx.get("data") and len(tx["data"]) >= 10:
        selector = tx["data"][:10]
        sig = await lookup_4byte(selector)
        warnings.append(f"function: {sig}")

        # Red flags
        if sig in ("setApprovalForAll(address,bool)", "approve(address,uint256)"):
            # check if amount is MAX_UINT
            warnings.append("⚠ APPROVAL request — verify spender")
        if "permit" in sig.lower():
            warnings.append("⚠ Permit (gasless approval) — verifikasi value")

    # Simulate
    try:
        result = w3.eth.call(tx)
    except Exception as e:
        warnings.append(f"⚠ Simulation failed: {e} — kemungkinan tx akan revert atau honeypot")

    return {"warnings": warnings, "decoded": sig if 'sig' in locals() else None}

async def lookup_4byte(selector: str) -> str:
    """Resolve function selector via 4byte directory."""
    import httpx
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://www.4byte.directory/api/v1/signatures/?hex_signature={selector}")
    results = r.json().get("results", [])
    return results[0]["text_signature"] if results else f"unknown ({selector})"
```

## EIP-1271 — Smart Contract Wallet Signatures

Safe (Gnosis), Argent, Sequence, dst pakai contract wallet (bukan EOA). Signature mereka harus diverifikasi lewat `isValidSignature(bytes32 hash, bytes signature)` di contract, bukan ecrecover.

```python
EIP1271_ABI = [{
    "name": "isValidSignature",
    "inputs": [{"name": "hash", "type": "bytes32"},
                {"name": "signature", "type": "bytes"}],
    "outputs": [{"name": "magic", "type": "bytes4"}],
    "type": "function", "stateMutability": "view",
}]

MAGIC_VALUE = "0x1626ba7e"

def verify_signature_universal(w3, address: str, hash32: bytes, signature: str) -> bool:
    """Verify signature dari EOA atau smart contract wallet."""
    code = w3.eth.get_code(address)
    if code == b'':
        # EOA — pakai ecrecover
        recovered = Account.recover_message(
            encode_defunct(primitive=hash32), signature=signature
        )
        return recovered.lower() == address.lower()
    else:
        # contract wallet — call isValidSignature
        c = w3.eth.contract(address=address, abi=EIP1271_ABI)
        try:
            magic = c.functions.isValidSignature(hash32, signature).call()
            return magic.hex() == MAGIC_VALUE
        except Exception:
            return False
```

## ENS / Address Resolution

ENS (Ethereum) — resolve `vitalik.eth` → address dan sebaliknya:

```python
from ens import ENS

ns = ENS.from_web3(w3)

# Forward: name → address
address = ns.address("vitalik.eth")

# Reverse: address → primary name
name = ns.name("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

# Text records (avatar, twitter, github, etc.)
avatar = ns.get_text("vitalik.eth", "avatar")
```

Untuk Solana (SNS — `.sol`): pakai `solana-name-service` SDK atau Bonfida API.
Untuk Sui: SuiNS. Untuk Aptos: ANS.

## Confirmation Template (untuk Hermes)

```
🔐 SIGN REQUEST
─────────────────────
dApp:       opensea.io
Domain:     opensea.io ✓ (verified)
Action:     Sign-In with Ethereum
Address:    0xAbCd...1234
Chain:      Ethereum (1)
Nonce:      a7c9...
─────────────
Statement:  Sign in to OpenSea
─────────────
Sign? (yes/no/show-raw)
```

```
🔐 TX REQUEST (via WalletConnect)
─────────────────────
dApp:       app.uniswap.org ✓
Function:   swapExactETHForTokens(...)
Value:      0.5 ETH
To:         Uniswap V2 Router ✓ (verified)
Path:       WETH → USDC
Min out:    ≈ 1200 USDC
─────────────
⚠ Simulation passed
─────────────
Sign? (yes/no/show-raw)
```
