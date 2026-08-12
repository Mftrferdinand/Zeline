# Wallet Management

Reference untuk membuat wallet baru, import dari seed phrase / private key, list wallet tersimpan, dan cek balance lintas chain.

## Buat Wallet Baru

### EVM (Ethereum + semua L2 + EVM sidechain)

```python
from eth_account import Account
import secrets

Account.enable_unaudited_hdwallet_features()

# Generate 24-word mnemonic + akun di derivation path Ethereum standar
mnemonic = Account.create_with_mnemonic(num_words=24, passphrase="")
acct, mnemonic_phrase = mnemonic

result = {
    "chain": "EVM",
    "address": acct.address,
    "private_key": acct.key.hex(),         # JANGAN PRINT
    "mnemonic": mnemonic_phrase,            # JANGAN PRINT
    "derivation_path": "m/44'/60'/0'/0/0",
}
```

Address EVM (0x...) yang sama bisa dipakai di Ethereum, BSC, Base, Arbitrum, Optimism, Polygon, Avalanche, Linea, Scroll, dll.

### Solana

```python
from solders.keypair import Keypair
from mnemonic import Mnemonic
from solders.keypair import Keypair
import hashlib

mnemo = Mnemonic("english")
phrase = mnemo.generate(strength=256)         # 24 kata
seed = mnemo.to_seed(phrase, passphrase="")
# Solana pakai ed25519, derivation path m/44'/501'/0'/0'
# Gunakan `hd-wallet-derive` atau implementasi BIP-44 ed25519
keypair = Keypair.from_seed(seed[:32])

result = {
    "chain": "Solana",
    "address": str(keypair.pubkey()),
    "private_key": keypair.secret().hex(),
    "mnemonic": phrase,
}
```

### Sui

```python
from pysui import SuiConfig
from pysui.sui.sui_crypto import create_new_keypair, SignatureScheme

keypair, mnemonic = create_new_keypair(scheme=SignatureScheme.ED25519)
address = keypair.to_sui_address()
```

### Aptos

```python
from aptos_sdk.account import Account

account = Account.generate()
# Untuk dapat mnemonic, gunakan Account.load_key dengan BIP39 separately
result = {
    "chain": "Aptos",
    "address": str(account.address()),
    "private_key": account.private_key.hex(),
}
```

### TON

```python
from tonsdk.crypto import mnemonic_new
from tonsdk.contract.wallet import Wallets, WalletVersionEnum

mnemonics = mnemonic_new(24)
_, pub_k, priv_k, wallet = Wallets.from_mnemonics(
    mnemonics, WalletVersionEnum.v4r2, workchain=0
)
address = wallet.address.to_string(is_user_friendly=True, is_bounceable=False)
```

## Import Wallet (dari seed phrase)

### EVM

```python
from eth_account import Account
Account.enable_unaudited_hdwallet_features()

# Cocok untuk seed yang di-generate Metamask, Rabby, Trust, dll.
acct = Account.from_mnemonic(
    mnemonic,
    passphrase="",                    # password tambahan kalau user pakai
    account_path="m/44'/60'/0'/0/0",  # ganti index terakhir untuk akun ke-N
)
```

Untuk mendapatkan banyak akun dari satu seed (umum di workflow multi-wallet):

```python
def derive_evm_accounts(mnemonic: str, count: int):
    return [
        Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{i}")
        for i in range(count)
    ]
```

### Solana / Sui / Aptos / TON

Lihat library masing-masing — semua pakai BIP-39 mnemonic, hanya derivation path-nya beda. Tabel cepat:

| Chain | Path |
|---|---|
| EVM | `m/44'/60'/0'/0/{i}` |
| Solana | `m/44'/501'/{i}'/0'` (Phantom-style) |
| Sui | `m/44'/784'/0'/0'/{i}'` |
| Aptos | `m/44'/637'/0'/0'/{i}'` |
| TON | tidak pakai BIP-44 standar; gunakan helper `tonsdk` |

## Import Wallet (dari private key)

```python
# EVM
acct = Account.from_key(private_key_hex)

# Solana
from solders.keypair import Keypair
kp = Keypair.from_bytes(bytes.fromhex(private_key_hex))
# atau dari base58 (format Phantom export):
import base58
kp = Keypair.from_bytes(base58.b58decode(b58_private_key))
```

## Storage: Wallet Registry

Hermes butuh tempat menyimpan banyak wallet user. **JANGAN simpan private key plaintext.**

```python
# scripts/wallet_manager.py pattern
import json, os
from cryptography.fernet import Fernet
from pathlib import Path

REGISTRY_PATH = Path.home() / ".hermes" / "wallets.enc"

def _get_cipher(master_password: str) -> Fernet:
    import base64, hashlib
    key = base64.urlsafe_b64encode(hashlib.sha256(master_password.encode()).digest())
    return Fernet(key)

def save_wallet(label: str, chain: str, address: str, private_key: str,
                mnemonic: str | None, master_password: str):
    cipher = _get_cipher(master_password)
    REGISTRY_PATH.parent.mkdir(exist_ok=True, parents=True)

    registry = load_registry(master_password) if REGISTRY_PATH.exists() else {}
    registry[label] = {
        "chain": chain,
        "address": address,
        "private_key_enc": cipher.encrypt(private_key.encode()).decode(),
        "mnemonic_enc": cipher.encrypt(mnemonic.encode()).decode() if mnemonic else None,
    }
    REGISTRY_PATH.write_bytes(cipher.encrypt(json.dumps(registry).encode()))

def load_registry(master_password: str) -> dict:
    cipher = _get_cipher(master_password)
    return json.loads(cipher.decrypt(REGISTRY_PATH.read_bytes()).decode())
```

Detail lengkap soal keamanan: `references/security.md`.

## Cek Balance Lintas Chain

```python
# EVM native balance
from web3 import Web3
w3 = Web3(Web3.HTTPProvider(rpc_url))
balance_wei = w3.eth.get_balance(address)
balance_eth = w3.from_wei(balance_wei, "ether")

# ERC-20 balance
ERC20_BALANCE_ABI = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
                     "type": "function"}]
token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_BALANCE_ABI)
raw = token.functions.balanceOf(address).call()
# decimals harus di-fetch terpisah atau di-cache
```

Untuk Solana / Sui / Aptos / TON, gunakan client masing-masing (`AsyncClient.get_balance`, dll). Untuk multi-chain dashboard cepat, pertimbangkan API agregator seperti Zerion, DeBank (EVM saja), atau Birdeye (Solana).

## Checklist Saat Buat/Import Wallet

- [ ] Tanya user mau chain mana (atau "all chains" untuk seed yang sama dipakai EVM-derived)
- [ ] Generate / import → simpan terenkripsi → JANGAN print private key/mnemonic ke chat
- [ ] Konfirmasi ke user: "wallet `{label}` berhasil dibuat, address `{address}`. Mnemonic sudah disimpan terenkripsi. Mau saya tampilkan sekali untuk dicatat?" → tampilkan hanya kalau user minta eksplisit
- [ ] Beri reminder: backup mnemonic secara offline, Hermes tidak bisa recover kalau master password hilang
