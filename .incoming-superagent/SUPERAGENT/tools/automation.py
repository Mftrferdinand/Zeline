"""
tools/automation.py — Event-Driven Automation Engine  (v4.0)

IFTTT buat crypto/ops: "KALAU [trigger] MAKA [rangkaian aksi]". Lebih kuat dari
alert (yang cuma notif) — ini alert yang BERTINDAK. Aksi yang nyentuh dana TETAP
lewat governor (via handler-nya). FROZEN (nge-drive eksekusi).

Trigger: webhook | schedule | price | onchain_event | custom.
Action: nama aksi + params; dieksekusi via handler registry yang operator wire.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable, Any, Optional

DEFAULT_DB = Path(os.environ.get("HERMES_AUTOMATION_DB", "~/.hermes/automation.db")).expanduser()
TRIGGERS = {"webhook", "schedule", "price", "onchain_event", "custom"}


@dataclass
class Rule:
    id: int
    name: str
    trigger: str
    trigger_params: dict
    actions: list          # [{"action": str, "params": {...}}, ...]
    active: int
    last_fired: float
    cooldown_s: int


class AutomationEngine:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, trigger TEXT,
            trigger_params TEXT, actions TEXT, active INTEGER DEFAULT 1,
            last_fired REAL DEFAULT 0, cooldown_s INTEGER DEFAULT 0)""")
        self.db.commit()

    def add_rule(self, name: str, trigger: str, trigger_params: dict,
                 actions: list[dict], cooldown_s: int = 0) -> int:
        if trigger not in TRIGGERS:
            raise ValueError(f"trigger tidak dikenal: {trigger}. {TRIGGERS}")
        cur = self.db.execute(
            "INSERT INTO rules(name, trigger, trigger_params, actions, cooldown_s) VALUES (?,?,?,?,?)",
            (name, trigger, json.dumps(trigger_params), json.dumps(actions), cooldown_s))
        self.db.commit()
        return cur.lastrowid

    def list_rules(self, only_active: bool = True) -> list[Rule]:
        q = "SELECT id,name,trigger,trigger_params,actions,active,last_fired,cooldown_s FROM rules"
        if only_active:
            q += " WHERE active=1"
        return [Rule(r[0], r[1], r[2], json.loads(r[3]), json.loads(r[4]), r[5], r[6], r[7])
                for r in self.db.execute(q).fetchall()]

    def _matches(self, rule: Rule, event: dict) -> bool:
        if event.get("trigger") != rule.trigger:
            return False
        # cocokin params trigger (subset match)
        for k, v in rule.trigger_params.items():
            if k in event and event[k] != v:
                return False
        return True

    async def fire(self, event: dict,
                   action_handlers: dict[str, Callable[[dict], Awaitable[Any]]],
                   confirm_cb: Optional[Callable[[dict], Awaitable[bool]]] = None) -> list[dict]:
        """Event masuk → rule yang cocok dijalankan aksinya. Return log."""
        log = []
        now = time.time()
        for rule in self.list_rules(only_active=True):
            if not self._matches(rule, event):
                continue
            if rule.cooldown_s and now - rule.last_fired < rule.cooldown_s:
                log.append({"rule": rule.name, "status": "cooldown"})
                continue
            for act in rule.actions:
                name = act.get("action")
                h = action_handlers.get(name)
                if h is None:
                    log.append({"rule": rule.name, "action": name, "status": "no_handler"})
                    continue
                # aksi nyentuh dana → handler-nya yang lewat governor; di sini konfirmasi opsional
                if confirm_cb is not None and act.get("funds"):
                    if not await confirm_cb({"rule": rule.name, "action": act}):
                        log.append({"rule": rule.name, "action": name, "status": "rejected"})
                        continue
                try:
                    res = await h(act.get("params", {}))
                    log.append({"rule": rule.name, "action": name, "status": "done", "res": str(res)[:80]})
                except Exception as e:
                    log.append({"rule": rule.name, "action": name, "status": "error", "err": repr(e)})
            self.db.execute("UPDATE rules SET last_fired=? WHERE id=?", (now, rule.id))
            self.db.commit()
        return log


if __name__ == "__main__":
    import asyncio, tempfile
    ae = AutomationEngine(Path(tempfile.mktemp()))
    ae.add_rule("gas murah → notify", "price", {"metric": "gas"},
                actions=[{"action": "notify", "params": {"msg": "gas lagi murah!"}}])
    ae.add_rule("deploy webhook → run", "webhook", {"hook": "ci_pass"},
                actions=[{"action": "deploy", "params": {"chain": "base"}, "funds": True}])
    async def main():
        handlers = {
            "notify": lambda p: asyncio.sleep(0, result=f"notified: {p['msg']}"),
            "deploy": lambda p: asyncio.sleep(0, result="deployed via governor"),
        }
        print("event gas:", await ae.fire({"trigger": "price", "metric": "gas"}, handlers))
        print("event webhook:", await ae.fire({"trigger": "webhook", "hook": "ci_pass"}, handlers,
                                               confirm_cb=lambda i: asyncio.sleep(0, result=True)))
    asyncio.run(main())
