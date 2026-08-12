"""
tools/briefing.py — Proactive Daily Briefing  (v4.0)

Push satu ringkasan tiap pagi (tanpa diminta): portfolio, gas, claim/task due,
proposal pending dari self-improve loop, lesson terbaru, jumlah alert aktif.

Bukan ngegantiin tool lain — dia NGEKOMPOS dari yang udah ada:
- memory_engine  → lesson terbaru + blocker/decision yang open
- reflection     → proposal yang nunggu review
- alerts         → jumlah rule aktif
- portfolio/gas  → inject provider lo (keyless: balanceOf multicall + eth_gasPrice)

Section bersifat plug-in: yang gak ada provider-nya di-skip, gak error.
Dijadwalin lewat cron/scheduler harian (lihat sk14.md). Guard once-per-day biar
gak dobel kalau heartbeat sering.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

STATE = Path(os.environ.get("HERMES_BRIEFING_STATE", "~/.hermes/briefing-last.txt")).expanduser()
PROPOSALS_DIR = Path(os.environ.get("HERMES_PROPOSALS", "~/.hermes/proposals")).expanduser()


@dataclass
class BriefingSection:
    title: str
    lines: list[str]


def _proposals_section() -> Optional[BriefingSection]:
    if not PROPOSALS_DIR.exists():
        return None
    files = sorted(PROPOSALS_DIR.glob("*.md"))
    if not files:
        return None
    lines = [f"• {f.stem}" for f in files[:5]]
    if len(files) > 5:
        lines.append(f"• … +{len(files) - 5} lagi")
    return BriefingSection(f"📝 Proposal nunggu review ({len(files)})", lines)


def _memory_sections(memory_engine) -> list[BriefingSection]:
    out = []
    lessons = memory_engine.recent(3, kind="lesson") if memory_engine else []
    if lessons:
        out.append(BriefingSection("🧠 Lesson terbaru",
                                   [f"• {m.content}" for m in lessons]))
    open_items = (memory_engine.recent(5, kind="blocker") if memory_engine else [])
    if open_items:
        out.append(BriefingSection("⏳ Masih open",
                                   [f"• {m.content}" for m in open_items[:3]]))
    return out


def _alerts_section(alert_engine) -> Optional[BriefingSection]:
    if not alert_engine:
        return None
    rules = alert_engine.list_rules(only_active=True)
    if not rules:
        return None
    return BriefingSection(f"🔔 Alert aktif ({len(rules)})",
                           [f"• [{r.label}] {r.kind}" for r in rules[:5]])


def build_briefing(
    memory_engine=None,
    alert_engine=None,
    portfolio_provider: Optional[Callable[[], list[str]]] = None,
    gas_provider: Optional[Callable[[], list[str]]] = None,
    extra_sections: Optional[list[BriefingSection]] = None,
) -> str:
    """Susun teks briefing. Section tanpa data di-skip."""
    today = time.strftime("%A, %d %b %Y")
    parts: list[BriefingSection] = []

    if portfolio_provider:
        try:
            parts.append(BriefingSection("💼 Portfolio", portfolio_provider()))
        except Exception:
            pass
    if gas_provider:
        try:
            parts.append(BriefingSection("⛽ Gas", gas_provider()))
        except Exception:
            pass
    a = _alerts_section(alert_engine)
    if a:
        parts.append(a)
    parts.extend(_memory_sections(memory_engine))
    p = _proposals_section()
    if p:
        parts.append(p)
    if extra_sections:
        parts.extend(extra_sections)

    if not parts:
        return f"☀️ Briefing {today}\n(tidak ada yang perlu dilaporkan)"

    out = [f"☀️ *Briefing {today}*", ""]
    for s in parts:
        out.append(f"*{s.title}*")
        out.extend(s.lines or ["—"])
        out.append("")
    return "\n".join(out).strip()


def already_ran_today() -> bool:
    if not STATE.exists():
        return False
    return STATE.read_text().strip() == time.strftime("%Y-%m-%d")


def _mark_ran():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(time.strftime("%Y-%m-%d"))


async def push_briefing(notifier, once_per_day: bool = True, **kwargs) -> Optional[str]:
    """Build + kirim via Notifier. once_per_day guard biar gak dobel."""
    if once_per_day and already_ran_today():
        return None
    text = build_briefing(**kwargs)
    if notifier:
        await notifier.send(text, severity="info")
    if once_per_day:
        _mark_ran()
    return text


if __name__ == "__main__":
    import sys, tempfile
    sys.path.insert(0, str(Path(__file__).parent))
    from memory_engine import MemoryEngine
    from alerts import AlertEngine
    me = MemoryEngine(Path(tempfile.mktemp()))
    me.remember("RPC ankr Base sering timeout → llamarpc", "lesson", "rpc,base", weight=2)
    me.remember("bridge ke Linea belum kelar", "blocker", "bridge,linea")
    ae = AlertEngine(Path(tempfile.mktemp()))
    ae.add_rule("price_below", {"token": "0xWETH", "threshold": 2000}, label="ETH dip")
    text = build_briefing(
        memory_engine=me, alert_engine=ae,
        portfolio_provider=lambda: ["• Total: $4,210 (+2.1%)", "• ETH 1.2 · USDC 1,800"],
        gas_provider=lambda: ["• Ethereum: 14 gwei (murah)", "• Base: 0.02 gwei"],
    )
    print(text)
