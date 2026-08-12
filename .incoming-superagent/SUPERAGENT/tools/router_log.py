#!/usr/bin/env python3
"""
tools/router_log.py — Router Decision Log & Disambiguation Tuner  (v4.2)

Nyatet tiap keputusan skill-router (AGENTS.md / sk0.md): skill mana yang kepilih,
skornya berapa, dan kalau ada TIE (dua skill skor mirip) yang bikin agent harus
nanya operator. Setelah kekumpul, `tune_report()` nunjuk pasangan skill yang sering
tabrakan → itu data buat nyetel ulang bobot keyword, bukan nebak.

Kenapa: dengan 30+ skill, keyword overlap (sk12 'bulk' vs sk30 'bulk', sk21 vs sk25
'enterprise') bikin router sering ragu. Log ini ngubah "perasaan" jadi angka.

Zero-dep (sqlite3/json/time). Connection injectable buat test.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_DB = Path(os.environ.get("SUPERAGENT_ROUTER_DB", "~/.superagent/router.db")).expanduser()
# Selisih skor <= TIE_MARGIN (fraksi dari skor primary) dianggap "tie/ambigu".
TIE_MARGIN = float(os.environ.get("SUPERAGENT_ROUTER_TIE_MARGIN", "0.2"))


@dataclass
class RouteDecision:
    text_excerpt: str
    primary: str
    primary_score: float
    runner_up: Optional[str]
    runner_up_score: float
    was_tie: bool


@dataclass
class TunePair:
    a: str
    b: str
    ties: int


class RouterLog:
    def __init__(self, db_path: Path = DEFAULT_DB, conn: Optional[sqlite3.Connection] = None,
                 tie_margin: float = TIE_MARGIN):
        self._lock = threading.Lock()
        self.tie_margin = tie_margin
        if conn is not None:
            self._db = conn
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS routes (
                   ts REAL, excerpt TEXT, primary_skill TEXT, primary_score REAL,
                   runner_up TEXT, runner_up_score REAL, was_tie INTEGER
               )"""
        )
        self._db.commit()

    @staticmethod
    def _is_tie(primary_score: float, runner_up_score: float, margin: float) -> bool:
        if primary_score <= 0 or runner_up_score <= 0:
            return False
        return (primary_score - runner_up_score) <= margin * primary_score

    def log(self, text: str, scores: dict) -> RouteDecision:
        """scores: {skill_id: score}. Ranking, deteksi tie, simpan, return keputusan."""
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        primary, p_score = (ranked[0] if ranked else ("", 0.0))
        runner, r_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))
        tie = self._is_tie(p_score, r_score, self.tie_margin)
        excerpt = (text or "")[:120]
        with self._lock:
            self._db.execute(
                "INSERT INTO routes VALUES (?,?,?,?,?,?,?)",
                (time.time(), excerpt, primary, p_score, runner, r_score, int(tie)),
            )
            self._db.commit()
        return RouteDecision(excerpt, primary, p_score, runner, r_score, tie)

    def tune_report(self, min_ties: int = 2) -> list:
        """Pasangan skill yang paling sering tie (kandidat retune bobot keyword)."""
        rows = self._db.execute(
            "SELECT primary_skill, runner_up, COUNT(*) FROM routes "
            "WHERE was_tie=1 AND runner_up IS NOT NULL "
            "GROUP BY primary_skill, runner_up"
        ).fetchall()
        pairs: dict = {}
        for prim, runner, n in rows:
            key = tuple(sorted([prim, runner]))
            pairs[key] = pairs.get(key, 0) + n
        out = [TunePair(a=k[0], b=k[1], ties=v) for k, v in pairs.items() if v >= min_ties]
        out.sort(key=lambda p: p.ties, reverse=True)
        return out

    def stats(self) -> dict:
        total = self._db.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
        ties = self._db.execute("SELECT COUNT(*) FROM routes WHERE was_tie=1").fetchone()[0]
        return {"total": total, "ties": ties,
                "tie_rate": round(ties / total, 3) if total else 0.0}

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "RouterLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


if __name__ == "__main__":
    rl = RouterLog(conn=sqlite3.connect(":memory:"))
    rl.log("garapan bulk scrape API", {"sk30": 9, "sk12": 8, "sk6": 3})
    rl.log("bulk parallel worker queue", {"sk12": 9, "sk30": 8})
    rl.log("audit compliance GDPR pipeline", {"sk25": 7, "sk21": 6})
    print(rl.stats())
    for p in rl.tune_report(min_ties=1):
        print(f"  {p.a} ⇄ {p.b}: {p.ties} ties → review bobot keyword")
