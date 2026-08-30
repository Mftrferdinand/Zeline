"""Token usage accounting, and cost only when the operator supplies prices.

`zeline stats` answers two practical questions: which model is eating tokens,
and how much has been spent. Zeline previously discarded the `usage` block that
providers return, so nothing could be answered at all.

Two design decisions worth stating plainly:

- **Cost is never invented.** Prices change constantly and differ per provider,
  route, and contract. Zeline reports *tokens* unconditionally and *money* only
  for models the operator priced in config. An unpriced model shows tokens with
  a blank cost rather than a confident wrong number.
- **Recording must never break a turn.** Every write is best-effort. A locked
  database or a malformed usage block loses a statistic, which is strictly
  better than losing the user's answer.

Usage rows are stored per model and per UTC day in `~/.zeline/usage.db`, with
the identity hashed exactly like sessions and memory so chat IDs never land in
another table.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zeline import config

_LOCK = threading.Lock()


def _db_path() -> Path:
    return config.DATA_DIR / "usage.db"


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def enabled() -> bool:
    return bool(getattr(config, "USAGE_TRACKING", True))


class UsageStore:
    """SQLite-backed usage log. Every operation degrades instead of raising."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS usage ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  day TEXT NOT NULL,"
                    "  model TEXT NOT NULL,"
                    "  key TEXT NOT NULL,"
                    "  prompt_tokens INTEGER NOT NULL DEFAULT 0,"
                    "  completion_tokens INTEGER NOT NULL DEFAULT 0,"
                    "  calls INTEGER NOT NULL DEFAULT 0,"
                    "  ts REAL NOT NULL"
                    ")"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model)")
        except sqlite3.Error:
            return
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        identity: str = "cli:local",
        ts: float | None = None,
    ) -> bool:
        """Append one usage row. Returns True when it was stored."""
        if not enabled():
            return False
        if prompt_tokens <= 0 and completion_tokens <= 0:
            # Nothing useful to record; a provider that omits usage should not
            # produce a stream of zero rows.
            return False
        moment = time.time() if ts is None else ts
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO usage (day, model, key, prompt_tokens, completion_tokens, calls, ts) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (
                        _day(moment),
                        str(model or "(unknown)"),
                        _key(identity),
                        max(0, int(prompt_tokens)),
                        max(0, int(completion_tokens)),
                        moment,
                    ),
                )
            return True
        except (sqlite3.Error, OSError, ValueError, TypeError):
            # Losing a statistic must never cost the user their answer.
            return False

    def _aggregate(self, group_column: str, since_day: str | None) -> list[dict[str, Any]]:
        query = (
            f"SELECT {group_column} AS bucket, SUM(prompt_tokens), SUM(completion_tokens), SUM(calls) "
            "FROM usage"
        )
        params: tuple[Any, ...] = ()
        if since_day:
            query += " WHERE day >= ?"
            params = (since_day,)
        query += f" GROUP BY {group_column} ORDER BY bucket DESC"
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                rows = conn.execute(query, params).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "bucket": str(row[0]),
                "prompt_tokens": int(row[1] or 0),
                "completion_tokens": int(row[2] or 0),
                "total_tokens": int(row[1] or 0) + int(row[2] or 0),
                "calls": int(row[3] or 0),
            }
            for row in rows
        ]

    def by_model(self, since_day: str | None = None) -> list[dict[str, Any]]:
        rows = self._aggregate("model", since_day)
        rows.sort(key=lambda item: item["total_tokens"], reverse=True)
        return rows

    def by_day(self, since_day: str | None = None) -> list[dict[str, Any]]:
        return self._aggregate("day", since_day)

    def totals(self, since_day: str | None = None) -> dict[str, int]:
        rows = self.by_model(since_day)
        return {
            "prompt_tokens": sum(row["prompt_tokens"] for row in rows),
            "completion_tokens": sum(row["completion_tokens"] for row in rows),
            "total_tokens": sum(row["total_tokens"] for row in rows),
            "calls": sum(row["calls"] for row in rows),
            "models": len(rows),
        }

    def clear(self) -> int:
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                cur = conn.execute("DELETE FROM usage")
                return cur.rowcount or 0
        except sqlite3.Error:
            return 0


# ------------------------------------------------------------------ extraction

def extract_usage(payload: Any, protocol: str = "openai") -> tuple[int, int]:
    """Pull (prompt_tokens, completion_tokens) out of a provider response.

    Handles the OpenAI shape (`usage.prompt_tokens` / `usage.completion_tokens`)
    and the Anthropic shape (`usage.input_tokens` / `usage.output_tokens`).
    Returns (0, 0) rather than raising when the provider omits usage — many
    OpenAI-compatible relays do.
    """
    if not isinstance(payload, dict):
        return 0, 0
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0

    def _int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    if protocol == "anthropic":
        return _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    if not prompt and not completion:
        # Some relays only send a total; attribute it to completion so the sum
        # stays truthful rather than silently dropping the number.
        total = _int(usage.get("total_tokens"))
        return 0, total
    return prompt, completion


# ----------------------------------------------------------------- cost pricing

def _prices() -> dict[str, dict[str, float]]:
    """Operator-supplied prices: {"model": {"input": 0.5, "output": 1.5}} per 1M."""
    raw = getattr(config, "MODEL_PRICES", None)
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, dict[str, float]] = {}
    for model, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            parsed[str(model)] = {
                "input": float(value.get("input", 0) or 0),
                "output": float(value.get("output", 0) or 0),
            }
        except (TypeError, ValueError):
            continue
    return parsed


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Cost in the operator's own currency unit, or None when unpriced.

    Returning None is deliberate: an invented price is worse than no number.
    Prices are configured per 1,000,000 tokens.
    """
    prices = _prices()
    entry = prices.get(model)
    if entry is None:
        # Allow a prefix match so 'gpt-4o' can price 'gpt-4o-2024-08-06'.
        for name, value in prices.items():
            if model.startswith(name):
                entry = value
                break
    if entry is None:
        return None
    return (prompt_tokens / 1_000_000 * entry["input"]) + (
        completion_tokens / 1_000_000 * entry["output"]
    )


def since_day_for(days: int | None) -> str | None:
    """First day to include for a 'last N days' window, or None for all time."""
    if not days or days <= 0:
        return None
    cutoff = time.time() - (days - 1) * 86400
    return _day(cutoff)


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)
