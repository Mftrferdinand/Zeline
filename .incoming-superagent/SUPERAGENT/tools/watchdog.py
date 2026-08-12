"""
tools/watchdog.py — Self-Healing Watchdog  (v4.0)

Mantau proses (bot sendiri + service yang lo daftarin), restart yang mati,
alert lewat Notifier. Reliability = manfaat harian: asisten yang sering mati
diam-diam gak kepake; yang jaga dirinya sendiri yang lo percaya.

GERBANG (penting): watchdog cuma nyentuh proses yang OPERATOR daftarin eksplisit
di config — bukan dari input mana pun. Restart di-rate-limit (max N/jam) biar
gak masuk loop restart. Ini selaras sama SAFE_AUTO_ACTIONS sk55 (restart_crashed_process).
Self-improve loop GAK BOLEH ngedit file ini (ada di FROZEN_PATHS reflection.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HEARTBEAT_FILE = Path(os.environ.get("HERMES_BOT_HEARTBEAT", "~/.hermes/bot-heartbeat")).expanduser()


@dataclass
class WatchedProcess:
    name: str
    # check_fn() -> True kalau hidup. restart_fn() -> None. Dua-duanya bisa dari shell helper.
    check_fn: Callable[[], bool]
    restart_fn: Callable[[], None]
    max_restarts_per_hour: int = 5
    _restart_times: list = field(default_factory=list)

    def _can_restart(self) -> bool:
        now = time.time()
        self._restart_times = [t for t in self._restart_times if now - t < 3600]
        return len(self._restart_times) < self.max_restarts_per_hour


def shell_check(pgrep_pattern: str) -> Callable[[], bool]:
    """check_fn berbasis pgrep. Pattern didefinisikan operator di config."""
    def _check() -> bool:
        if shutil.which("pgrep") is None:
            return True  # gak bisa cek → anggap hidup, jangan restart sembarangan
        r = subprocess.run(["pgrep", "-f", pgrep_pattern], capture_output=True)
        return r.returncode == 0
    return _check


def shell_restart(command: list[str]) -> Callable[[], None]:
    """restart_fn berbasis command list (BUKAN string shell — hindari injection)."""
    def _restart() -> None:
        subprocess.run(command, capture_output=True, timeout=60)
    return _restart


class Watchdog:
    def __init__(self, processes: Optional[list[WatchedProcess]] = None):
        self.processes = processes or []

    def watch(self, proc: WatchedProcess):
        self.processes.append(proc)

    def bot_heartbeat_alive(self, max_stale_s: int = 300) -> bool:
        """Bot nge-touch HEARTBEAT_FILE tiap loop. Kalau basi > max_stale → bot hang."""
        if not HEARTBEAT_FILE.exists():
            return False
        return (time.time() - HEARTBEAT_FILE.stat().st_mtime) < max_stale_s

    @staticmethod
    def touch_heartbeat():
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(str(time.time()))

    def tick(self) -> list[dict]:
        """Satu siklus cek. Return daftar event (buat di-alert pemanggil)."""
        events = []
        for p in self.processes:
            try:
                alive = p.check_fn()
            except Exception as e:
                events.append({"proc": p.name, "status": "check_error", "detail": repr(e)})
                continue
            if alive:
                continue
            if not p._can_restart():
                events.append({"proc": p.name, "status": "down_ratelimited",
                               "detail": f"sudah restart {p.max_restarts_per_hour}x/jam — STOP, butuh operator"})
                continue
            try:
                p.restart_fn()
                p._restart_times.append(time.time())
                events.append({"proc": p.name, "status": "restarted"})
            except Exception as e:
                events.append({"proc": p.name, "status": "restart_failed", "detail": repr(e)})
        return events

    async def run(self, notifier=None, interval_s: int = 30):
        import asyncio
        while True:
            for ev in self.tick():
                sev = "critical" if ev["status"] in ("down_ratelimited", "restart_failed") else "warn"
                msg = f"watchdog: {ev['proc']} → {ev['status']}" + \
                      (f" ({ev.get('detail')})" if ev.get("detail") else "")
                if notifier:
                    await notifier.send(msg, severity=sev)
                else:
                    print(msg)
            await asyncio.sleep(interval_s)


if __name__ == "__main__":
    # simulasi: satu proses 'mati' yang restart-nya nambahin counter
    state = {"alive": False, "restarts": 0}
    def restart():
        state["restarts"] += 1
        state["alive"] = True
    wd = Watchdog([WatchedProcess("fake-bot",
                                  check_fn=lambda: state["alive"],
                                  restart_fn=restart, max_restarts_per_hour=2)])
    print("tick 1 (mati):", wd.tick(), "| restarts:", state["restarts"])
    state["alive"] = False
    print("tick 2 (mati lagi):", wd.tick(), "| restarts:", state["restarts"])
    state["alive"] = False
    print("tick 3 (rate-limited):", wd.tick(), "| restarts:", state["restarts"])
