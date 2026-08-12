"""
tools/dashboard.py — Live Web Dashboard  (v4.0)

Bikin HTML dashboard self-contained (no external dep) dari data agent: portfolio,
gas, alert aktif, proposal pending, status governor, audit ringkas. Di-serve dari
VPS (sk9/sk2). Read-only.

build_dashboard_html(data) -> str  (pure, gampang di-tes).
gather(...) ngumpulin dari engine yang ada (opsional, butuh DB/network).

KEAMANAN: kalau di-serve, kasih auth + jangan expose public polos (dashboard
nampilin posisi & aktivitas). Default localhost.
"""
from __future__ import annotations

import html
import json
import time
from typing import Optional

_CSS = """
:root{--bg:#0e0c0d;--card:#17141a;--fg:#ece8f0;--mut:#8a8594;--acc:#FF5A36;--ok:#3ecf8e;--warn:#ffb454}
*{box-sizing:border-box;margin:0;font-family:ui-monospace,Menlo,monospace}
body{background:var(--bg);color:var(--fg);padding:20px;max-width:1100px;margin:auto}
h1{font-size:20px;margin-bottom:2px}.sub{color:var(--mut);font-size:12px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.card{background:var(--card);border:1px solid #241f29;border-radius:12px;padding:16px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin-bottom:10px}
.row{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid #201c26}
.row:last-child{border:0}.big{font-size:26px;font-weight:700}.acc{color:var(--acc)}.ok{color:var(--ok)}.warn{color:var(--warn)}
.tag{display:inline-block;background:#241f29;border-radius:6px;padding:2px 8px;font-size:11px;margin:2px}
"""


def _card(title: str, rows_html: str) -> str:
    return f'<div class="card"><h2>{html.escape(title)}</h2>{rows_html}</div>'


def _rows(items: list[tuple[str, str]]) -> str:
    return "".join(f'<div class="row"><span>{html.escape(str(k))}</span>'
                   f'<span>{html.escape(str(v))}</span></div>' for k, v in items)


def build_dashboard_html(data: dict) -> str:
    """data: {portfolio, gas, alerts, proposals, governor, audit, version}."""
    cards = []
    pf = data.get("portfolio") or {}
    if pf:
        cards.append(_card("Portfolio",
            f'<div class="big acc">{html.escape(str(pf.get("total","-")))}</div>'
            + _rows(list(pf.get("breakdown", {}).items()))))
    gas = data.get("gas") or {}
    if gas:
        cards.append(_card("Gas", _rows(list(gas.items()))))
    gov = data.get("governor") or {}
    if gov:
        halted = gov.get("halted")
        status = '<span class="warn">HALTED</span>' if halted else '<span class="ok">OK</span>'
        cards.append(_card("Governor",
            f'<div class="row"><span>status</span><span>{status}</span></div>'
            + _rows([("sesi terpakai", f"${gov.get('session_spent_usd',0)}")])))
    alerts = data.get("alerts") or []
    if alerts:
        cards.append(_card(f"Alert aktif ({len(alerts)})",
            "".join(f'<span class="tag">{html.escape(a)}</span>' for a in alerts[:12])))
    props = data.get("proposals", 0)
    cards.append(_card("Self-improve",
        _rows([("proposal pending", props),
               ("auto-fix", data.get("audit", {}).get("autofixes", 0)),
               ("frozen-write diblok", data.get("audit", {}).get("frozen_write_blocks", 0))])))

    ver = data.get("version", "v4.0")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SUPERAGENT {html.escape(ver)}</title><style>{_CSS}</style></head><body>
<h1>SUPERAGENT <span class="acc">{html.escape(ver)}</span></h1>
<div class="sub">live dashboard · {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
<div class="grid">{''.join(cards)}</div>
<script>setTimeout(()=>location.reload(),30000)</script>
</body></html>"""


def gather(governor=None, alert_engine=None, memory_engine=None,
           portfolio_provider=None, gas_provider=None) -> dict:
    data = {"version": "v4.0"}
    try:
        if governor: data["governor"] = governor.report()
    except Exception: pass
    try:
        if alert_engine: data["alerts"] = [f"{r.label}" for r in alert_engine.list_rules()]
    except Exception: pass
    try:
        if portfolio_provider: data["portfolio"] = portfolio_provider()
    except Exception: pass
    try:
        if gas_provider: data["gas"] = gas_provider()
    except Exception: pass
    try:
        from explain import summary_dict
        data["audit"] = summary_dict()
        data["proposals"] = summary_dict().get("proposals", 0)
    except Exception: pass
    return data


if __name__ == "__main__":
    demo = {
        "version": "v4.0",
        "portfolio": {"total": "$4,210 (+2.1%)", "breakdown": {"ETH": "1.2", "USDC": "1,800"}},
        "gas": {"Ethereum": "14 gwei", "Base": "0.02 gwei"},
        "governor": {"halted": None, "session_spent_usd": 120.0},
        "alerts": ["ETH dip", "gas murah", "LZ claim"],
        "proposals": 1,
        "audit": {"autofixes": 3, "frozen_write_blocks": 1},
    }
    out = build_dashboard_html(demo)
    open("/tmp/dash.html", "w").write(out)
    print("html len:", len(out), "| punya semua section:",
          all(x in out for x in ["Portfolio", "Gas", "Governor", "Alert aktif", "Self-improve"]))
