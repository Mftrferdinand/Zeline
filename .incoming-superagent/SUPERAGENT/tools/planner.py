"""
tools/planner.py — Natural-Language Workflow Planner  (v4.0)

Operator ngomong tujuan → agent dekomposisi jadi rangkaian langkah dari skill
library, tampilin plan, eksekusi bertahap dengan checkpoint. Langkah yang nyentuh
dana TETAP lewat governor.

Decomposition pakai LLM (via model_registry cascade) yang diminta balikin JSON
plan; ada validasi step lawan skill yang dikenal + fallback. FROZEN (ngatur
eksekusi multi-step).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional, Awaitable, Any

# Skill/aksi yang dikenal planner. Tiap step harus map ke salah satu ini.
KNOWN_ACTIONS = {
    "swap", "bridge", "mint", "snipe", "stake", "claim_airdrop", "deploy_contract",
    "read_contract", "write_contract", "check_balance", "monitor", "notify",
    "transcribe", "report", "wait", "custom",
}
FUND_ACTIONS = {"swap", "bridge", "mint", "snipe", "stake", "claim_airdrop",
                "deploy_contract", "write_contract"}

PLAN_SYS = (
    "You are a planner for a crypto agent. Decompose the user's goal into an ordered "
    "JSON array of steps. Each step: {\"action\": one of " + ",".join(sorted(KNOWN_ACTIONS)) +
    ", \"params\": {...}, \"why\": \"...\"}. Output ONLY JSON, no prose.")


@dataclass
class Step:
    action: str
    params: dict
    why: str = ""

    @property
    def touches_funds(self) -> bool:
        return self.action in FUND_ACTIONS


@dataclass
class Plan:
    goal: str
    steps: list[Step]
    def summary(self) -> str:
        out = [f"📋 Plan: {self.goal}", ""]
        for i, s in enumerate(self.steps, 1):
            flag = " 💸" if s.touches_funds else ""
            out.append(f"{i}. {s.action}{flag}  {s.params}  — {s.why}")
        return "\n".join(out)


def make_plan(goal: str, llm_fn: Optional[Callable[[list], str]] = None) -> Plan:
    """llm_fn(messages)->str. Default: model_registry cascade. Validasi step."""
    if llm_fn is None:
        from model_registry import ModelRegistry
        reg = ModelRegistry()
        llm_fn = lambda msgs: reg.call_with_cascade(msgs)[0]
    raw = llm_fn([{"role": "system", "content": PLAN_SYS},
                  {"role": "user", "content": goal}])
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(raw)
    steps = []
    for s in data:
        act = s.get("action", "custom")
        if act not in KNOWN_ACTIONS:
            act = "custom"
        steps.append(Step(act, s.get("params", {}), s.get("why", "")))
    return Plan(goal, steps)


@dataclass
class StepResult:
    step: Step
    status: str        # "done" | "blocked" | "rejected" | "error" | "skipped"
    detail: str = ""


async def execute_plan(plan: Plan,
                       handlers: dict[str, Callable[[dict], Awaitable[Any]]],
                       confirm_cb: Optional[Callable[[Step], Awaitable[bool]]] = None,
                       stop_on_error: bool = True) -> list[StepResult]:
    """
    Jalanin tiap step via handler yang operator wire. Step fund → handler-nya
    WAJIB lewat governor (planner gak bypass). Checkpoint: stop kalau error.
    """
    results = []
    for step in plan.steps:
        handler = handlers.get(step.action)
        if handler is None:
            results.append(StepResult(step, "skipped", "handler belum di-wire"))
            if stop_on_error:
                break
            continue
        if step.touches_funds and confirm_cb is not None:
            if not await confirm_cb(step):
                results.append(StepResult(step, "rejected", "user nolak step"))
                break
        try:
            res = await handler(step.params)
            results.append(StepResult(step, "done", str(res)[:120]))
        except Exception as e:
            results.append(StepResult(step, "error", repr(e)))
            if stop_on_error:
                break
    return results


if __name__ == "__main__":
    import asyncio
    # mock LLM yang balikin plan JSON
    mock_plan = json.dumps([
        {"action": "check_balance", "params": {"chain": "base"}, "why": "pastikan ada gas"},
        {"action": "bridge", "params": {"to": "base", "amount": 0.1}, "why": "isi base"},
        {"action": "swap", "params": {"token": "0xABC", "amount": 0.05}, "why": "beli target"},
        {"action": "notify", "params": {"msg": "selesai"}, "why": "lapor"},
    ])
    plan = make_plan("isi base lalu beli token 0xABC", llm_fn=lambda m: mock_plan)
    print(plan.summary())
    async def main():
        log = []
        handlers = {
            "check_balance": lambda p: log.append("balance ok") or "ok",
            "bridge": lambda p: log.append("bridged") or "tx1",
            "swap": lambda p: log.append("swapped") or "tx2",
            "notify": lambda p: log.append("notified") or "sent",
        }
        # bungkus sync jadi async
        import asyncio
        ah = {k: (lambda p, f=v: asyncio.sleep(0, result=f(p))) for k, v in handlers.items()}
        res = await execute_plan(plan, ah, confirm_cb=lambda s: asyncio.sleep(0, result=True))
        print("\nexecution:")
        for r in res:
            print(f"  {r.step.action}: {r.status} ({r.detail})")
    asyncio.run(main())
