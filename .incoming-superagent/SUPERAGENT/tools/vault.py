"""
tools/vault.py — Snippet/Address Vault + Macros  (v4.0)

Simpen yang sering dipakai: alamat ("wallet kerja"), template, command, dan
MACRO (workflow multi-step bernama). "kirim ke wallet kerja" → resolve label
ke address; "jalanin morning routine" → ambil step macro-nya.

SAFETY: vault cuma NYIMPEN & ngeresolve. Address hasil resolve, pas dipakai di
tx, TETAP lewat governor + konfirmasi. File ini ada di FROZEN_PATHS — self-improve
loop gak boleh ngeditnya (kalau bisa, dia bisa diam-diam ganti "wallet kerja" jadi
alamat attacker → vektor serius). Perubahan logika cuma lewat operator.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

Kind = Literal["address", "snippet", "command", "template"]
DEFAULT_DB = Path(os.environ.get("HERMES_VAULT_DB", "~/.hermes/vault.db")).expanduser()
_ADDR_RE = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")  # EVM / base58-ish


@dataclass
class Entry:
    label: str
    kind: str
    value: str
    tags: str


class Vault:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS entries (
            label TEXT PRIMARY KEY, kind TEXT, value TEXT, tags TEXT DEFAULT '')""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS macros (
            name TEXT PRIMARY KEY, steps TEXT, note TEXT DEFAULT '')""")
        self.db.commit()

    # ---- entries ----
    def put(self, label: str, value: str, kind: Kind = "snippet", tags: str = ""):
        if kind == "address" and not _ADDR_RE.match(value):
            raise ValueError(f"'{value}' gak kelihatan kayak address valid (EVM/base58)")
        self.db.execute(
            "INSERT OR REPLACE INTO entries(label, kind, value, tags) VALUES (?,?,?,?)",
            (label.lower(), kind, value, tags))
        self.db.commit()

    def get(self, label: str) -> Optional[Entry]:
        r = self.db.execute(
            "SELECT label, kind, value, tags FROM entries WHERE label = ?",
            (label.lower(),)).fetchone()
        return Entry(*r) if r else None

    def resolve_address(self, label: str) -> Optional[str]:
        e = self.get(label)
        if e and e.kind == "address":
            return e.value
        return None

    def list(self, kind: Optional[Kind] = None) -> list[Entry]:
        q = "SELECT label, kind, value, tags FROM entries"
        args: list = []
        if kind:
            q += " WHERE kind = ?"; args.append(kind)
        return [Entry(*r) for r in self.db.execute(q, args).fetchall()]

    def remove(self, label: str):
        self.db.execute("DELETE FROM entries WHERE label = ?", (label.lower(),))
        self.db.commit()

    # ---- macros ----
    def add_macro(self, name: str, steps: list[str], note: str = ""):
        """steps = list instruksi bahasa natural / nama aksi. Eksekusi oleh pemanggil
        (tiap step yang nyentuh dana tetap lewat governor)."""
        self.db.execute("INSERT OR REPLACE INTO macros(name, steps, note) VALUES (?,?,?)",
                        (name.lower(), json.dumps(steps), note))
        self.db.commit()

    def get_macro(self, name: str) -> Optional[list[str]]:
        r = self.db.execute("SELECT steps FROM macros WHERE name = ?", (name.lower(),)).fetchone()
        return json.loads(r[0]) if r else None

    def list_macros(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT name FROM macros").fetchall()]


if __name__ == "__main__":
    import tempfile
    v = Vault(Path(tempfile.mktemp()))
    v.put("wallet kerja", "0x52908400098527886E0F7030069857D2E4169EE7", "address", "main")
    v.put("gm template", "gm fam, wagmi 🚀", "template")
    v.add_macro("morning routine",
                ["cek portfolio", "cek gas semua chain", "cek claim window airdrop", "kirim briefing"],
                note="rutinitas pagi")
    print("resolve 'wallet kerja':", v.resolve_address("wallet kerja"))
    print("macro 'morning routine':", v.get_macro("morning routine"))
    try:
        v.put("bad addr", "bukan-address", "address")
    except ValueError as e:
        print("address validation works:", str(e)[:40], "...")
