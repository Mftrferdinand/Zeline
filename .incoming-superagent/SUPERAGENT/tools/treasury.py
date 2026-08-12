#!/usr/bin/env python3
"""
tools/treasury.py — Treasury & P&L Ledger (V7)
Treasury management, cashflow tracking, P&L auto-generation.
Daily/weekly/monthly profit reports. Revenue stream optimization.
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Wallet:
    name: str
    address: str
    tier: str  # hot | warm | cold
    chain: str
    balance_usd: float = 0.0
    last_updated: str = ""

@dataclass
class Transaction:
    id: str
    timestamp: str
    type: str  # revenue | cost | transfer
    amount_usd: float
    asset: str
    chain: str
    category: str  # mev | airdrop | yield | nft | gas | api | infra | other
    project: str = ""
    tx_hash: str = ""
    notes: str = ""

class Treasury:
    """Treasury & P&L management."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or Path.home() / ".agent" / "treasury"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.wallets: dict[str, Wallet] = {}
        self.transactions: list[Transaction] = []
        self._load()

    def _load(self):
        tx_file = self.data_dir / "transactions.jsonl"
        if tx_file.exists():
            with open(tx_file) as f:
                for line in f:
                    if line.strip():
                        self.transactions.append(Transaction(**json.loads(line)))

        w_file = self.data_dir / "wallets.json"
        if w_file.exists():
            with open(w_file) as f:
                for w in json.load(f):
                    self.wallets[w["address"]] = Wallet(**w)

    def save(self):
        with open(self.data_dir / "transactions.jsonl", "w") as f:
            for t in self.transactions:
                f.write(json.dumps(t.__dict__) + "\n")

        with open(self.data_dir / "wallets.json", "w") as f:
            json.dump([w.__dict__ for w in self.wallets.values()], f, indent=2)

    def add_wallet(self, wallet: Wallet):
        self.wallets[wallet.address] = wallet
        self.save()

    def log_tx(self, tx: Transaction):
        self.transactions.append(tx)
        self.save()

    def total_balance(self) -> float:
        return sum(w.balance_usd for w in self.wallets.values())

    def total_value_by_tier(self) -> dict[str, float]:
        tiers = {}
        for w in self.wallets.values():
            tiers[w.tier] = tiers.get(w.tier, 0) + w.balance_usd
        return tiers

    def pnl_report(self, period: str = "daily") -> dict:
        """Generate P&L report: daily, weekly, monthly."""
        now = datetime.now(timezone.utc)
        if period == "daily":
            since = now - timedelta(days=1)
        elif period == "weekly":
            since = now - timedelta(days=7)
        elif period == "monthly":
            since = now - timedelta(days=30)
        else:
            since = now - timedelta(days=1)

        txs = [t for t in self.transactions if t.timestamp >= since.isoformat()[:19]]
        revenue = sum(t.amount_usd for t in txs if t.type == "revenue")
        costs = sum(t.amount_usd for t in txs if t.type == "cost")
        net = revenue - costs

        by_category = {}
        for t in txs:
            by_category[t.category] = by_category.get(t.category, 0) + t.amount_usd

        return {
            "period": period,
            "from": since.isoformat()[:10],
            "to": now.isoformat()[:10],
            "total_balance": self.total_balance(),
            "revenue": round(revenue, 2),
            "costs": round(costs, 2),
            "net_profit": round(net, 2),
            "roi_pct": round((net / costs * 100) if costs else 0, 2),
            "by_category": {k: round(v, 2) for k, v in by_category.items()},
            "tiers": self.total_value_by_tier(),
            "tx_count": len(txs),
        }

    def revenue_streams_report(self) -> dict:
        """Analyze revenue stream performance."""
        now = datetime.now(timezone.utc)
        month_ago = now - timedelta(days=30)
        txs = [t for t in self.transactions if t.timestamp >= month_ago.isoformat()[:19]]

        streams = {}
        for t in txs:
            if t.type == "revenue":
                s = streams.setdefault(t.category, {"total": 0, "count": 0, "avg": 0})
                s["total"] += t.amount_usd
                s["count"] += 1

        for s in streams.values():
            s["avg"] = round(s["total"] / s["count"], 2) if s["count"] else 0
            s["total"] = round(s["total"], 2)

        # Sort by total desc
        ranked = sorted(streams.items(), key=lambda x: x[1]["total"], reverse=True)

        return {
            "period": "30d",
            "streams": {k: v for k, v in ranked},
            "top_stream": ranked[0][0] if ranked else "none",
            "total_revenue": round(sum(s["total"] for _, s in ranked), 2),
        }

    def auto_revenue_check(self) -> list[dict]:
        """Check for auto-revenue opportunities."""
        opportunities = []

        # Check idle wallets (>7 days no activity)
        now = datetime.now(timezone.utc)
        for w in self.wallets.values():
            if w.balance_usd > 100:  # Worth moving
                last_tx = max(
                    (t for t in self.transactions if t.tx_hash and w.address.lower() in str(t).lower()),
                    key=lambda t: t.timestamp,
                    default=None,
                )
                if last_tx and (now - datetime.fromisoformat(last_tx.timestamp)).days > 7:
                    opportunities.append({
                        "type": "idle_wallet",
                        "wallet": w.name,
                        "balance": w.balance_usd,
                        "chain": w.chain,
                        "suggestion": "Deploy to yield strategy or consolidate",
                    })

        return opportunities


# ─── CLI ───
if __name__ == "__main__":
    import sys
    t = Treasury()

    if len(sys.argv) < 2:
        # Default: show daily P&L
        print(json.dumps(t.pnl_report("daily"), indent=2))
    elif sys.argv[1] == "pnl":
        period = sys.argv[2] if len(sys.argv) > 2 else "daily"
        print(json.dumps(t.pnl_report(period), indent=2))
    elif sys.argv[1] == "streams":
        print(json.dumps(t.revenue_streams_report(), indent=2))
    elif sys.argv[1] == "opportunities":
        print(json.dumps(t.auto_revenue_check(), indent=2))
    elif sys.argv[1] == "balance":
        print(f"Total: ${t.total_balance():,.2f}")
        for tier, val in t.total_value_by_tier().items():
            print(f"  {tier}: ${val:,.2f}")
