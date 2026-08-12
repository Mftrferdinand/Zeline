"""
hermes/wallet_manager.py — Template wallet management lintas-chain.

Usage:
    wm = WalletManager(master_password=os.environ["HERMES_MASTER_PW"])
    wm.create("main_evm", chain="evm")
    wm.import_from_mnemonic("trader_sol", "word word word...", chain="solana")
    accts = wm.derive_many("hd_seed", count=20, chain="evm")
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
import base64


Chain = Literal["evm", "solana", "sui", "aptos", "ton"]


@dataclass
class Wallet:
    label: str
    chain: Chain
    address: str
    private_key: str           # decrypted only in memory, briefly
    mnemonic: Optional[str]


class WalletManager:
    """Encrypted vault untuk wallet user. Pakai Scrypt KDF + Fernet."""

    DEFAULT_PATH = Path.home() / ".hermes" / "vault.enc"

    def __init__(self, master_password: str, vault_path: Optional[Path] = None):
        self.path = vault_path or self.DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = self._make_cipher(master_password)

    # ---------- crypto ----------
    def _make_cipher(self, password: str) -> Fernet:
        if self.path.exists() and self.path.stat().st_size >= 16:
            salt = self.path.read_bytes()[:16]
        else:
            salt = os.urandom(16)
            self.path.write_bytes(salt)  # placeholder header
        kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1, backend=default_backend())
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)

    def _load(self) -> dict:
        data = self.path.read_bytes()
        if len(data) <= 16:
            return {}
        return json.loads(self._fernet.decrypt(data[16:]).decode())

    def _save(self, registry: dict):
        salt = self.path.read_bytes()[:16]
        enc = self._fernet.encrypt(json.dumps(registry).encode())
        self.path.write_bytes(salt + enc)

    # ---------- public API ----------
    def create(self, label: str, chain: Chain) -> Wallet:
        if chain == "evm":
            w = self._create_evm()
        elif chain == "solana":
            w = self._create_solana()
        elif chain == "sui":
            w = self._create_sui()
        elif chain == "aptos":
            w = self._create_aptos()
        elif chain == "ton":
            w = self._create_ton()
        else:
            raise ValueError(f"unsupported chain: {chain}")

        registry = self._load()
        if label in registry:
            raise ValueError(f"label '{label}' already exists")
        registry[label] = {
            "chain": chain,
            "address": w.address,
            "private_key": w.private_key,
            "mnemonic": w.mnemonic,
        }
        self._save(registry)
        return Wallet(label=label, **registry[label])

    def import_from_mnemonic(self, label: str, mnemonic: str, chain: Chain,
                              account_index: int = 0) -> Wallet:
        if chain == "evm":
            w = self._import_evm_mnemonic(mnemonic, account_index)
        elif chain == "solana":
            w = self._import_solana_mnemonic(mnemonic, account_index)
        else:
            raise NotImplementedError(f"mnemonic import for {chain} — see references/wallets.md")

        registry = self._load()
        registry[label] = {
            "chain": chain, "address": w.address,
            "private_key": w.private_key, "mnemonic": mnemonic,
        }
        self._save(registry)
        return Wallet(label=label, **registry[label])

    def import_from_private_key(self, label: str, private_key: str, chain: Chain) -> Wallet:
        if chain == "evm":
            from eth_account import Account
            acct = Account.from_key(private_key)
            address = acct.address
        elif chain == "solana":
            import base58
            from solders.keypair import Keypair
            try:
                kp = Keypair.from_bytes(bytes.fromhex(private_key))
            except ValueError:
                kp = Keypair.from_bytes(base58.b58decode(private_key))
            address = str(kp.pubkey())
        else:
            raise NotImplementedError(f"private key import for {chain}")

        registry = self._load()
        registry[label] = {
            "chain": chain, "address": address,
            "private_key": private_key, "mnemonic": None,
        }
        self._save(registry)
        return Wallet(label=label, chain=chain, address=address,
                      private_key=private_key, mnemonic=None)

    def derive_many(self, label_prefix: str, count: int, chain: Chain,
                    from_existing_label: Optional[str] = None,
                    mnemonic: Optional[str] = None) -> list[Wallet]:
        """Derive N akun dari satu mnemonic. Pakai mnemonic dari label yg sudah ada
        atau dari arg `mnemonic`."""
        if from_existing_label:
            existing = self._load()[from_existing_label]
            mnemonic = existing["mnemonic"]
        assert mnemonic, "butuh mnemonic baik dari label atau argument"

        out = []
        for i in range(count):
            w = self.import_from_mnemonic(f"{label_prefix}_{i}", mnemonic, chain, account_index=i)
            out.append(w)
        return out

    def list_wallets(self) -> list[dict]:
        """Return ringkasan (TANPA private key)."""
        return [
            {"label": k, "chain": v["chain"], "address": v["address"]}
            for k, v in self._load().items()
        ]

    def get(self, label: str) -> Wallet:
        v = self._load()[label]
        return Wallet(label=label, **v)

    def delete(self, label: str):
        r = self._load()
        r.pop(label, None)
        self._save(r)

    # ---------- per-chain constructors ----------
    def _create_evm(self) -> Wallet:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        acct, phrase = Account.create_with_mnemonic(num_words=24)
        return Wallet(label="", chain="evm", address=acct.address,
                      private_key=acct.key.hex(), mnemonic=phrase)

    def _import_evm_mnemonic(self, mnemonic: str, idx: int) -> Wallet:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        acct = Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{idx}")
        return Wallet(label="", chain="evm", address=acct.address,
                      private_key=acct.key.hex(), mnemonic=mnemonic)

    def _create_solana(self) -> Wallet:
        from mnemonic import Mnemonic
        from solders.keypair import Keypair
        phrase = Mnemonic("english").generate(strength=256)
        seed = Mnemonic("english").to_seed(phrase)
        kp = Keypair.from_seed(seed[:32])
        return Wallet(label="", chain="solana", address=str(kp.pubkey()),
                      private_key=bytes(kp).hex(), mnemonic=phrase)

    def _import_solana_mnemonic(self, mnemonic: str, idx: int) -> Wallet:
        # CATATAN: derivation Solana yang persis Phantom butuh BIP44 ed25519 lib
        # (`bip-utils`). Implementasi minimal di bawah pakai seed pertama saja (idx=0).
        from mnemonic import Mnemonic
        from solders.keypair import Keypair
        if idx != 0:
            raise NotImplementedError("multi-account Solana — pakai bip_utils library "
                                       "(lihat references/wallets.md)")
        seed = Mnemonic("english").to_seed(mnemonic)
        kp = Keypair.from_seed(seed[:32])
        return Wallet(label="", chain="solana", address=str(kp.pubkey()),
                      private_key=bytes(kp).hex(), mnemonic=mnemonic)

    def _create_sui(self) -> Wallet:
        # Placeholder — pakai pysui sesuai references/wallets.md
        raise NotImplementedError("install pysui; lihat references/wallets.md")

    def _create_aptos(self) -> Wallet:
        from aptos_sdk.account import Account as AptosAccount
        acct = AptosAccount.generate()
        return Wallet(label="", chain="aptos", address=str(acct.address()),
                      private_key=acct.private_key.hex(), mnemonic=None)

    def _create_ton(self) -> Wallet:
        from tonsdk.crypto import mnemonic_new
        from tonsdk.contract.wallet import Wallets, WalletVersionEnum
        mnemonics = mnemonic_new(24)
        _, _, priv, wallet = Wallets.from_mnemonics(
            mnemonics, WalletVersionEnum.v4r2, workchain=0)
        addr = wallet.address.to_string(is_user_friendly=True, is_bounceable=False)
        return Wallet(label="", chain="ton", address=addr,
                      private_key=priv.hex(), mnemonic=" ".join(mnemonics))


if __name__ == "__main__":
    # Quick smoke test (jangan dijalankan di production tanpa master pw beneran)
    import sys
    if len(sys.argv) < 2:
        print("usage: python wallet_manager.py <command> [args]")
        sys.exit(1)

    pw = os.environ.get("HERMES_MASTER_PW")
    if not pw:
        pw = input("Master password: ")

    wm = WalletManager(pw)
    cmd = sys.argv[1]

    if cmd == "create":
        chain = sys.argv[2]
        label = sys.argv[3]
        w = wm.create(label, chain=chain)
        print(f"Created {label} on {chain}: {w.address}")
        print("Mnemonic disimpan terenkripsi. Ketik 'show' untuk lihat sekali:")
        if input("> ").strip() == "show":
            print(w.mnemonic)
    elif cmd == "list":
        for w in wm.list_wallets():
            print(f"  {w['label']:<20} {w['chain']:<8} {w['address']}")
    else:
        print(f"unknown: {cmd}")
