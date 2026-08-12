"""
tools/backtest.py — Strategy Backtester  (v4.0)

Replay strategi lawan data harga historis sebelum live. Pure computation, keyless,
fully offline. Strategi = fn(state, candle) -> "buy"|"sell"|"hold".

Metrik: return %, max drawdown, win rate, jumlah trade. Bukan jaminan masa depan —
alat validasi, bukan ramalan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Trade:
    side: str        # "buy" | "sell"
    price: float
    ts: int
    pnl: float = 0.0


@dataclass
class BacktestResult:
    initial: float
    final: float
    trades: list
    def metrics(self) -> dict:
        ret = (self.final / self.initial - 1) * 100 if self.initial else 0
        sells = [t for t in self.trades if t.side == "sell"]
        wins = [t for t in sells if t.pnl > 0]
        # max drawdown dari equity curve
        return {
            "return_pct": round(ret, 2),
            "trades": len(self.trades),
            "closed": len(sells),
            "win_rate_pct": round(100 * len(wins) / len(sells), 1) if sells else 0.0,
            "final_balance": round(self.final, 2),
        }


def backtest(strategy: Callable[[dict, dict], str],
             candles: list[dict],          # [{"ts":int,"price":float}, ...]
             initial: float = 1000.0,
             fee_pct: float = 0.3) -> BacktestResult:
    """
    strategy(state, candle) -> "buy"|"sell"|"hold". state mutable buat strategi.
    Sederhana: full-position long/flat. Buy = pakai semua cash; sell = jual semua.
    """
    cash, units, entry = initial, 0.0, 0.0
    trades, state = [], {}
    peak = initial
    max_dd = 0.0
    for c in candles:
        price = c["price"]
        equity = cash + units * price
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
        sig = strategy(state, c)
        if sig == "buy" and cash > 0:
            units = (cash * (1 - fee_pct / 100)) / price
            entry = price
            trades.append(Trade("buy", price, c["ts"]))
            cash = 0.0
        elif sig == "sell" and units > 0:
            proceeds = units * price * (1 - fee_pct / 100)
            pnl = proceeds - (units * entry)
            trades.append(Trade("sell", price, c["ts"], pnl=pnl))
            cash, units = proceeds, 0.0
    final = cash + units * (candles[-1]["price"] if candles else 0)
    res = BacktestResult(initial, final, trades)
    res._max_dd = round(max_dd * 100, 2)  # type: ignore
    return res


if __name__ == "__main__":
    # strategi SMA-cross sederhana di data sintetis
    prices = [100, 102, 101, 105, 110, 108, 112, 120, 118, 115, 122, 130, 125, 119, 128]
    candles = [{"ts": i, "price": p} for i, p in enumerate(prices)]

    def sma_cross(state, c):
        hist = state.setdefault("hist", [])
        hist.append(c["price"])
        if len(hist) < 5:
            return "hold"
        sma = sum(hist[-5:]) / 5
        if c["price"] > sma and not state.get("in"):
            state["in"] = True; return "buy"
        if c["price"] < sma and state.get("in"):
            state["in"] = False; return "sell"
        return "hold"

    res = backtest(sma_cross, candles, initial=1000)
    print("metrics:", res.metrics())
    print("max drawdown %:", res._max_dd)
    print("trades:", [(t.side, t.price) for t in res.trades])
