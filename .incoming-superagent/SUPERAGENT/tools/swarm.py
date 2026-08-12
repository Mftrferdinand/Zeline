"""
tools/swarm.py — Multi-Agent Swarm Orchestrator  (v4.0)

Koordinator yang nge-spawn specialist lane paralel (researcher/executor/monitor/...)
lalu agregasi hasil. In-process & ringan; kompatibel dengan delegate architecture
OpenClaw kalau mau di-scale ke proses terpisah.

Prinsip aman: lane yang KE-EXPOSE jaringan terus (watcher/monitor) sebaiknya gak
pegang key — pisahin dari lane executor (yang sign, lewat governor). FROZEN.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any, Optional


@dataclass
class Specialist:
    role: str                                   # "researcher" | "executor" | "monitor" | ...
    handler: Callable[[dict], Awaitable[Any]]   # async fn(task) -> result
    holds_keys: bool = False                     # True hanya untuk lane executor


@dataclass
class LaneResult:
    role: str
    status: str        # "ok" | "error" | "timeout"
    result: Any = None
    error: str = ""
    seconds: float = 0.0


class Swarm:
    def __init__(self, specialists: Optional[list[Specialist]] = None):
        self.specialists = {s.role: s for s in (specialists or [])}

    def add(self, s: Specialist):
        self.specialists[s.role] = s

    async def _run_one(self, role: str, task: dict, timeout: float) -> LaneResult:
        s = self.specialists[role]
        t0 = time.time()
        try:
            res = await asyncio.wait_for(s.handler(task), timeout=timeout)
            return LaneResult(role, "ok", res, seconds=round(time.time() - t0, 2))
        except asyncio.TimeoutError:
            return LaneResult(role, "timeout", seconds=round(time.time() - t0, 2))
        except Exception as e:
            return LaneResult(role, "error", error=repr(e), seconds=round(time.time() - t0, 2))

    async def dispatch(self, tasks: dict[str, dict], timeout: float = 60.0) -> dict[str, LaneResult]:
        """Jalanin banyak lane PARALEL. tasks = {role: task_params}. Return per role."""
        roles = [r for r in tasks if r in self.specialists]
        results = await asyncio.gather(*[self._run_one(r, tasks[r], timeout) for r in roles])
        return {r.role: r for r in results}

    async def pipeline(self, order: list[str], seed: dict, timeout: float = 60.0) -> list[LaneResult]:
        """Jalanin lane BERURUTAN — output satu jadi input berikutnya."""
        out, payload = [], seed
        for role in order:
            if role not in self.specialists:
                continue
            r = await self._run_one(role, payload, timeout)
            out.append(r)
            if r.status != "ok":
                break
            payload = {"prev": r.result, **seed}
        return out

    def safety_report(self) -> dict:
        """Cek pemisahan: lane non-executor idealnya gak holds_keys."""
        leak = [r for r, s in self.specialists.items() if s.holds_keys and r != "executor"]
        return {"lanes": list(self.specialists),
                "key_holding_lanes": [r for r, s in self.specialists.items() if s.holds_keys],
                "warning": (f"lane {leak} pegang key padahal bukan executor — pisahin" if leak else None)}


if __name__ == "__main__":
    async def researcher(t): await asyncio.sleep(0.01); return f"riset: {t.get('topic')}"
    async def monitor(t): await asyncio.sleep(0.01); return "gas 12 gwei, aman"
    async def executor(t): await asyncio.sleep(0.01); return "tx terkirim (via governor)"
    sw = Swarm([
        Specialist("researcher", researcher),
        Specialist("monitor", monitor),
        Specialist("executor", executor, holds_keys=True),
    ])
    async def main():
        res = await sw.dispatch({"researcher": {"topic": "LayerZero"}, "monitor": {}})
        for role, r in res.items():
            print(f"  [{role}] {r.status}: {r.result} ({r.seconds}s)")
        print("safety:", sw.safety_report())
        # bocor: kasih key ke monitor → harus diwarning
        sw.add(Specialist("monitor", monitor, holds_keys=True))
        print("after leak:", sw.safety_report()["warning"])
    asyncio.run(main())
