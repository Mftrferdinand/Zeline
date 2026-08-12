# Security & Secret Handling

Reference untuk bagaimana Hermes harus menangani private key, seed phrase, dan credential lain.

## Aturan Mutlak

1. **JANGAN PERNAH log private key atau mnemonic.** Cek setiap `print`, `logging.info`, `logger.debug` — pastikan tidak ada string ini ter-leak. Lebih baik log address saja.

2. **JANGAN PERNAH kirim secret ke service eksternal.** Tidak ke API LLM, tidak ke logging service (Sentry, Datadog), tidak ke webhook user. Kalau perlu signature/tx, sign **lokal** lalu kirim hasil signed-nya saja.

3. **JANGAN simpan secret di plaintext file.** Selalu enkripsi.

4. **Master password tidak boleh hardcoded.** Minta dari env var atau prompt interactive sekali per sesi.

5. **Tidak ada "share private key supaya saya bisa bantu."** Kalau user paste private key di chat, jangan repeat back, jangan simpan di log, langsung process → lalu remind user untuk rotate kalau private key itu sudah ke-share di chat (potensi terlihat di log provider).

## Encrypted Storage

Pattern minimal dengan `cryptography` (PyPI):

```python
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.backends import default_backend
import base64, os, json
from pathlib import Path

class Vault:
    """Encrypted wallet store. File single, salt prepended."""

    def __init__(self, path: Path, master_password: str):
        self.path = path
        self._fernet = self._derive_cipher(master_password)

    def _derive_cipher(self, password: str) -> Fernet:
        # Salt disimpan di file. Kalau file belum ada, generate baru.
        if self.path.exists():
            with open(self.path, "rb") as f:
                salt = f.read(16)
        else:
            salt = os.urandom(16)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "wb") as f:
                f.write(salt)  # placeholder header, data ditulis pas save

        kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1, backend=default_backend())
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)

    def save(self, data: dict):
        encrypted = self._fernet.encrypt(json.dumps(data).encode())
        # rewrite: salt (16 byte) + encrypted blob
        salt = self.path.read_bytes()[:16]
        self.path.write_bytes(salt + encrypted)

    def load(self) -> dict:
        if self.path.stat().st_size <= 16:
            return {}
        with open(self.path, "rb") as f:
            f.read(16)  # skip salt
            encrypted = f.read()
        return json.loads(self._fernet.decrypt(encrypted).decode())
```

## Memory Hygiene

Python tidak punya secure-erase native, tapi minimisasi exposure:

```python
def use_private_key_briefly(encrypted_blob: bytes, master_pw: str, callback):
    """Decrypt key, run callback, then drop reference."""
    key = decrypt(encrypted_blob, master_pw)
    try:
        return callback(key)
    finally:
        # overwrite reference; GC akan reclaim
        key = None
```

Untuk paranoid mode: jalankan signing di subprocess yang langsung mati setelah selesai.

## Hardware Wallet Support (Recommended)

Untuk wallet bernilai signifikan, support hardware wallet jauh lebih aman:

```python
# Ledger via `ledgereth` package
from ledgereth import create_transaction
from ledgereth.accounts import get_account_by_path

acct = get_account_by_path("44'/60'/0'/0/0")
signed = create_transaction(
    destination="0x...",
    amount=w3.to_wei(0.1, "ether"),
    gas=21000,
    gas_price=w3.eth.gas_price,
    nonce=nonce,
    chain_id=1,
    sender_path="44'/60'/0'/0/0",
)
w3.eth.send_raw_transaction(signed.raw_transaction())
```

Hermes harus tawarkan ini untuk wallet "treasury" yang holding besar.

## Audit Log (yang Aman)

```python
import logging

class SafeFormatter(logging.Formatter):
    """Auto-redact pattern yang look like private key / mnemonic."""
    SECRET_PATTERNS = [
        # 0x + 64 hex
        (r"0x[a-fA-F0-9]{64}", "0x[REDACTED]"),
        # 12 / 24 word phrase (sederhana, lengthnya rough)
        (r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b", "[MNEMONIC_REDACTED]"),
        # Base58 Solana private key (88 char)
        (r"[1-9A-HJ-NP-Za-km-z]{87,88}", "[B58_REDACTED]"),
    ]
    def format(self, record):
        import re
        msg = super().format(record)
        for pat, repl in self.SECRET_PATTERNS:
            msg = re.sub(pat, repl, msg)
        return msg

handler = logging.FileHandler(".hermes/audit.log")
handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(message)s"))
logger = logging.getLogger("hermes")
logger.addHandler(handler)
```

## Phishing-Resistant Confirmations

Sebelum sign tx ke contract yang user kasih sebagai input:

```python
def warn_if_suspicious_contract(w3, contract_addr: str) -> list[str]:
    warnings = []
    
    # 1. Address mirip token populer? (kemungkinan typo-squat)
    KNOWN = {"USDC": "0xA0b8...", "USDT": "0xdAC1...", "WETH": "0xC02a..."}
    for name, real in KNOWN.items():
        if levenshtein(contract_addr.lower(), real.lower()) <= 3:
            warnings.append(f"Address mirip {name} ({real}) — typo-squat?")
    
    # 2. Contract baru deploy?
    # cek block deploy via etherscan API atau eth_getCode + scan history
    
    # 3. Verified di Etherscan?
    # Etherscan API: getsourcecode → kalau SourceCode kosong = unverified
    
    return warnings
```

## User Confirmation Flow (untuk tx high-value)

```
⚠  HIGH-VALUE TX CONFIRMATION
─────────────
Value:      5.2 ETH (≈$13,000)
To:         0xAbCd...1234 (unverified contract on Etherscan)
Action:     Call swap() with 5.2 ETH
─────────────
Verifikasi: paste 4 karakter terakhir address tujuan untuk konfirmasi:
```

Ini memaksa user betulan baca address, bukan asal "yes". Pattern dipakai banyak hardware wallet UI.

## Secret Rotation

Kalau user mencurigai private key terkompromi (misalnya pernah di-paste di chat yang dilog di tempat lain):

```python
async def emergency_rotate(w3, old_account, new_address: str):
    """Move semua dana dari old → new dalam satu sesi."""
    # 1. transfer semua ERC-20 (iterasi token holdings via Alchemy / Moralis)
    # 2. transfer NFT (ERC-721 + ERC-1155)
    # 3. sweep native (sisakan gas)
    # 4. mark old wallet "deprecated" di registry
```

Hermes sebaiknya tawarkan ini tiap kali user paste private key langsung di chat.

## Checklist Pre-Production

- [ ] Master password disimpan di env var, bukan code
- [ ] Audit log pakai `SafeFormatter` (atau equivalent)
- [ ] Encryption file tidak ter-commit ke git (`.gitignore` `.hermes/`)
- [ ] Backup encrypted vault ke offline storage
- [ ] Test recovery: bisakah restore wallet dari mnemonic kalau vault hilang?
- [ ] No telemetry/analytics service yang bisa lihat memory/log dengan secret
