"""
tools/explain.py — Explainability / Audit Trail  (v4.0)

Rangkum apa yang agent lakuin & kenapa, dari audit trail yang udah ada:
- reflection-audit.log  (auto-fix, proposal, lesson, kill-switch trip)
- governor spend (kalau dikasih)
- automation log

Transparansi = kepercayaan. Read-only, gak ngubah apa-apa.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

AUDIT_LOG = Path(os.environ.get("HERMES_REFLECTION_AUDIT", "~/.hermes/reflection-audit.log")).expanduser()

EVENT_LABEL = {
    "AUTOFIX_EXECUTED": "🔧 auto-fix dijalankan",
    "AUTOFIX_REFUSED": "⛔ auto-fix ditolak (bukan safe action)",
    "PROPOSAL_WRITTEN": "📝 proposal dibuat",
    "LESSON_LEARNED": "🧠 lesson dipelajari",
    "BLOCKED_FROZEN_WRITE": "🛑 percobaan edit file frozen DIBLOK",
    "DAILY_CYCLE": "🔁 siklus refleksi harian",
}


def read_audit(limit: int = 50, audit_path: Path = AUDIT_LOG) -> list[dict]:
    if not audit_path.exists():
        return []
    lines = audit_path.read_text().strip().splitlines()[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def explain(limit: int = 50, audit_path: Path = AUDIT_LOG) -> str:
    events = read_audit(limit, audit_path)
    if not events:
        return "Belum ada aktivitas otonom tercatat."
    out = [f"🔍 Audit trail ({len(events)} event terakhir):", ""]
    for e in events:
        label = EVENT_LABEL.get(e.get("event"), e.get("event", "?"))
        ts = e.get("ts", "")
        detail = {k: v for k, v in e.items() if k not in ("ts", "event")}
        detail_str = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:3])
        out.append(f"[{ts}] {label}" + (f" — {detail_str}" if detail_str else ""))
    # ringkasan jenis
    counts = {}
    for e in events:
        counts[e.get("event")] = counts.get(e.get("event"), 0) + 1
    out += ["", "Ringkasan: " + ", ".join(f"{k}×{v}" for k, v in counts.items())]
    return "\n".join(out)


def summary_dict(limit: int = 200, audit_path: Path = AUDIT_LOG) -> dict:
    events = read_audit(limit, audit_path)
    counts = {}
    for e in events:
        counts[e.get("event", "?")] = counts.get(e.get("event", "?"), 0) + 1
    return {"total": len(events), "by_type": counts,
            "frozen_write_blocks": counts.get("BLOCKED_FROZEN_WRITE", 0),
            "autofixes": counts.get("AUTOFIX_EXECUTED", 0),
            "proposals": counts.get("PROPOSAL_WRITTEN", 0)}


if __name__ == "__main__":
    import tempfile
    p = Path(tempfile.mktemp())
    p.write_text("\n".join(json.dumps(x) for x in [
        {"ts": "2026-06-03 07:00:00", "event": "DAILY_CYCLE", "lessons_learned": []},
        {"ts": "2026-06-03 07:05:00", "event": "AUTOFIX_EXECUTED", "action": "rotate_rpc"},
        {"ts": "2026-06-03 07:06:00", "event": "BLOCKED_FROZEN_WRITE", "path": "tools/governor.py"},
        {"ts": "2026-06-03 07:10:00", "event": "PROPOSAL_WRITTEN", "title": "fallback RPC"},
    ]) + "\n")
    print(explain(audit_path=p))
    print("\nsummary:", summary_dict(audit_path=p))
