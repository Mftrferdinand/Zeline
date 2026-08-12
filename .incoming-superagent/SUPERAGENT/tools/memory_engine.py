"""
tools/memory_engine.py — Compounding Memory  (v4.0)

Substrat buat agent "makin pinter tiap hari": nyimpen fakta, pelajaran, preferensi,
keputusan, dan blocker+resolusi — lalu nge-recall yang relevan pas dibutuhin.

Keyless & lokal: SQLite + scoring keyword/recency/weight. Gak ada embedding API,
gak ada vendor. Cukup buat personal agent, dan kompatibel sama memory/*.md flow lo
(lihat export_markdown()).

Konsep "belajar": tiap memory punya `weight`. Pas sebuah memory kepake & kebukti
berguna → reinforce() naikin weight-nya → makin sering muncul di recall. Yang gak
pernah relevan → decay pelan. Jadi memory yang berguna naik ke atas seiring waktu.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

Kind = Literal["fact", "lesson", "preference", "decision", "blocker"]
DEFAULT_DB = Path(os.environ.get("HERMES_MEMORY_DB", "~/.hermes/memory.db")).expanduser()
_WORD = re.compile(r"[a-z0-9]{2,}")
_STOP = {"the", "and", "for", "yang", "dan", "untuk", "gak", "aja", "ini", "itu", "ke", "di"}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


@dataclass
class Memory:
    id: int
    ts: float
    kind: str
    tags: str
    content: str
    weight: float
    uses: int


class MemoryEngine:
    HALF_LIFE_DAYS = 30.0   # recency decay

    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, kind TEXT, tags TEXT, content TEXT,
                weight REAL DEFAULT 1.0, uses INTEGER DEFAULT 0
            )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_kind ON memories(kind)")
        self.db.commit()

    # ---- write ----
    def remember(self, content: str, kind: Kind = "fact",
                 tags: str = "", weight: float = 1.0) -> int:
        # dedupe ringan: kalau ada memory identik, reinforce aja
        row = self.db.execute(
            "SELECT id FROM memories WHERE content = ? AND kind = ?", (content, kind)).fetchone()
        if row:
            self.reinforce(row[0])
            return row[0]
        cur = self.db.execute(
            "INSERT INTO memories(ts, kind, tags, content, weight, uses) VALUES (?,?,?,?,?,0)",
            (time.time(), kind, tags, content, weight))
        self.db.commit()
        return cur.lastrowid

    def reinforce(self, mem_id: int, by: float = 0.5):
        self.db.execute(
            "UPDATE memories SET weight = weight + ?, uses = uses + 1 WHERE id = ?", (by, mem_id))
        self.db.commit()

    def forget(self, mem_id: int):
        self.db.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        self.db.commit()

    # ---- read / recall ----
    def _score(self, m: Memory, q_tokens: set[str], now: float) -> float:
        if not q_tokens:
            return 0.0
        m_tokens = set(_tokens(m.content + " " + m.tags))
        overlap = len(q_tokens & m_tokens)
        if overlap == 0:
            return 0.0
        age_days = (now - m.ts) / 86400
        recency = 0.5 ** (age_days / self.HALF_LIFE_DAYS)
        return overlap * (1 + math.log1p(m.weight)) * (0.5 + 0.5 * recency)

    def recall(self, query: str, k: int = 5,
               kind: Optional[Kind] = None, reinforce_hits: bool = True) -> list[Memory]:
        now = time.time()
        q = "SELECT id, ts, kind, tags, content, weight, uses FROM memories"
        args: list = []
        if kind:
            q += " WHERE kind = ?"; args.append(kind)
        rows = [Memory(*r) for r in self.db.execute(q, args).fetchall()]
        scored = sorted(((self._score(m, set(_tokens(query)), now), m) for m in rows),
                        key=lambda x: x[0], reverse=True)
        hits = [m for s, m in scored if s > 0][:k]
        if reinforce_hits:
            for m in hits:
                self.reinforce(m.id, by=0.1)   # dipakai = makin relevan
        return hits

    def recent(self, k: int = 10, kind: Optional[Kind] = None) -> list[Memory]:
        q = "SELECT id, ts, kind, tags, content, weight, uses FROM memories"
        args: list = []
        if kind:
            q += " WHERE kind = ?"; args.append(kind)
        q += " ORDER BY ts DESC LIMIT ?"; args.append(k)
        return [Memory(*r) for r in self.db.execute(q, args).fetchall()]

    # ---- compat dengan memory/*.md ----
    def export_markdown(self) -> str:
        out = ["# Memory snapshot", ""]
        for kind in ("decision", "preference", "lesson", "blocker", "fact"):
            mems = self.recent(50, kind=kind)
            if not mems:
                continue
            out.append(f"## {kind}")
            for m in sorted(mems, key=lambda x: x.weight, reverse=True):
                t = time.strftime("%Y-%m-%d", time.localtime(m.ts))
                out.append(f"- [{t}] (w{m.weight:.1f}) {m.content}")
            out.append("")
        return "\n".join(out)

    def stats(self) -> dict:
        return {k: self.db.execute("SELECT COUNT(*) FROM memories WHERE kind=?", (k,)).fetchone()[0]
                for k in ("fact", "lesson", "preference", "decision", "blocker")}


if __name__ == "__main__":
    import tempfile
    me = MemoryEngine(Path(tempfile.mktemp()))
    me.remember("operator gak suka pakai psk2, prefer systemd", "preference", "vps,deploy")
    me.remember("RPC publik ankr sering timeout di Base, pakai llamarpc dulu", "lesson", "rpc,base")
    me.remember("pakai postgres buat project X", "decision", "project-x,db")
    me.remember("Base RPC timeout lagi pas bridge", "blocker", "rpc,base,bridge")
    hits = me.recall("kenapa rpc base lambat")
    print("recall 'rpc base lambat':")
    for m in hits:
        print(f"  [{m.kind}] {m.content}  (score-ranked)")
    print("stats:", json.dumps(me.stats()))
