"""Sub-agent delegation: parallel workers, roles, and an optional verifier.

``delegate_task`` used to run exactly one sub-agent, in-line, with the parent's
own instructions. That is fine for offloading a single chore, but it is not a
team: three independent questions cost three sequential round trips, and nothing
ever checks the answer before it reaches the parent.

This module adds the two pieces that actually change output quality:

**Parallel workers.** Independent subtasks run at the same time instead of one
after another. Concurrency is capped (``tools.max_parallel_subagents``) because
each worker is a full agent making its own provider calls -- unbounded fan-out
would hammer the API and, on a rate-limited key, finish slower than running them
in sequence.

**A verifier pass.** One extra sub-agent whose only job is to check the workers'
output against the goal and say plainly what is wrong. This is the part that
makes multi-agent output better; more workers alone just produces more
unchecked text. It is opt-in per call, because it costs a round trip and most
delegations do not need it.

Three details that are easy to get wrong:

- **Each worker gets its own identity.** Workers share nothing, but they each
  construct a ``MemoryStore`` keyed by identity, so reusing one identity across
  parallel workers would have them writing the same memory file concurrently.
  Every worker gets a distinct ``::sub`` suffix.
- **One worker failing does not lose the others.** Each result is captured
  independently and reported per task, so a crash in task 2 still returns 1 and
  3. Losing completed work because a sibling failed would be the worst possible
  trade for going parallel.
- **A verifier that fails is not fatal.** If verification cannot run, the worker
  output is still returned, labelled as unverified. The verifier improves an
  answer; it must not be able to destroy one.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Any

from zeline import config

# What each role tells its sub-agent to optimise for. Roles are deliberately
# behavioural instructions, not different tools: a "coder" that could not read
# the web, or a "researcher" that could not read a file, would be less useful
# than one agent with all of them and a clear brief.
ROLE_BRIEFS: dict[str, str] = {
    "worker": "",
    "coder": (
        " You are acting as the CODER. Read the relevant code before changing it, "
        "match the conventions already in the file, and verify your change actually "
        "runs (build, tests, or an import) before reporting success. Report exact "
        "file paths and what you ran."
    ),
    "researcher": (
        " You are acting as the RESEARCHER. Gather evidence from multiple sources, "
        "prefer primary sources, and attribute every claim to where you found it. "
        "State plainly what you could NOT establish rather than filling the gap."
    ),
    "reviewer": (
        " You are acting as the REVIEWER. Do not rewrite the work; judge it. "
        "Point at specific problems with file and line where possible, separate "
        "certain defects from suspicions, and say clearly if you found nothing wrong."
    ),
    "writer": (
        " You are acting as the WRITER. Turn the material you are given into one "
        "coherent answer. Do not invent facts that are not in the material, and "
        "carry over concrete details (paths, numbers, names) exactly."
    ),
}

DEFAULT_ROLE = "worker"

VERIFIER_BRIEF = (
    " You are acting as the VERIFIER. You are given a goal and the output produced "
    "for it. Check the output against the goal: is it complete, internally "
    "consistent, and supported by evidence you can confirm with your tools? "
    "Check claims you can actually check (does that file exist, does that command "
    "run, does that number add up). Reply in this shape:\n"
    "VERDICT: ok | problems\n"
    "then, if problems, a short numbered list of what is wrong or unproven. "
    "Do not redo the work and do not pad the answer -- if it is sound, say so briefly."
)


def max_parallel() -> int:
    try:
        value = int(getattr(config, "MAX_PARALLEL_SUBAGENTS", 3))
    except (TypeError, ValueError):
        value = 3
    return max(1, min(value, 8))


@dataclass
class Task:
    goal: str
    context: str = ""
    role: str = DEFAULT_ROLE

    @property
    def clean_role(self) -> str:
        role = (self.role or DEFAULT_ROLE).strip().lower()
        return role if role in ROLE_BRIEFS else DEFAULT_ROLE


@dataclass
class Result:
    task: Task
    index: int
    output: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def parse_tasks(raw: Any) -> tuple[list[Task], str | None]:
    """Turn the tool's ``tasks`` argument into Task objects.

    Returns (tasks, error). Providers hand this over as parsed JSON, but a model
    may send a JSON *string* instead, so both are accepted -- rejecting a
    well-meant string would just cost a retry.
    """
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [], "tasks must be a list of objects with a 'goal' field."
    if not isinstance(raw, list) or not raw:
        return [], "tasks must be a non-empty list of objects with a 'goal' field."

    tasks: list[Task] = []
    for position, item in enumerate(raw, start=1):
        if isinstance(item, str):
            # A bare string is unambiguous: it is the goal.
            goal, context, role = item, "", DEFAULT_ROLE
        elif isinstance(item, dict):
            goal = str(item.get("goal", "")).strip()
            context = str(item.get("context", "") or "")
            role = str(item.get("role", DEFAULT_ROLE) or DEFAULT_ROLE)
        else:
            return [], f"task {position} must be an object with a 'goal' field."
        if not goal.strip():
            return [], f"task {position} is missing a non-empty 'goal'."
        tasks.append(Task(goal=goal.strip(), context=context, role=role))
    return tasks, None


def build_brief(task: Task) -> str:
    if not task.context.strip():
        return task.goal
    return (
        f"{task.goal}\n\n---\nContext you must use (you have no other memory of "
        f"the parent conversation):\n{task.context.strip()}"
    )


def system_extra_for(role: str, *, verifier: bool = False) -> str:
    base = (
        "\n\nYou are a SUB-AGENT spawned to complete ONE focused task and report back. "
        "You have no memory of the parent conversation beyond the brief you were given. "
        "Do the work with your tools, then reply with a concise, self-contained final "
        "summary of what you found or did (include concrete results, file paths, or key "
        "findings the caller needs). Do not ask the caller questions — decide and act."
    )
    if verifier:
        return base + VERIFIER_BRIEF
    return base + ROLE_BRIEFS.get(role, "")


def run_tasks(
    tasks: list[Task],
    *,
    spawn: Any,
    limit: int | None = None,
) -> list[Result]:
    """Run tasks, in parallel when there is more than one.

    ``spawn(task, index) -> str`` does the actual work; it is injected so this
    stays testable without launching real agents. Exceptions from spawn are
    captured per task: one failure must never discard a sibling's finished work.
    """
    if not tasks:
        return []
    if len(tasks) == 1:
        # No pool for a single task: a thread would add nothing but a frame.
        return [_run_one(tasks[0], 0, spawn)]

    workers = min(len(tasks), limit or max_parallel())
    results: list[Result | None] = [None] * len(tasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, task, index, spawn): index
            for index, task in enumerate(tasks)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 — defensive; _run_one catches already
                results[index] = Result(
                    task=tasks[index], index=index,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
    return [item for item in results if item is not None]


def _run_one(task: Task, index: int, spawn: Any) -> Result:
    try:
        output = spawn(task, index)
    except Exception as exc:  # noqa: BLE001 — arbitrary sub-agent failure
        return Result(task=task, index=index, error=f"{exc.__class__.__name__}: {exc}")
    text = (output or "").strip()
    if not text:
        return Result(task=task, index=index, output="(the sub-agent returned no output)")
    return Result(task=task, index=index, output=text)


def render(results: list[Result], *, verification: str = "", verified: bool | None = None) -> str:
    """Format results for the parent agent.

    Failures are reported alongside successes rather than replacing them, and the
    verification state is always stated explicitly -- silence about whether an
    answer was checked is worse than either answer.
    """
    if not results:
        return "ERROR: no sub-agent tasks ran."

    if len(results) == 1 and not verification and verified is None:
        single = results[0]
        if not single.ok:
            return f"ERROR: sub-agent failed: {single.error}"
        return f"[sub-agent result]\n{single.output}"

    lines: list[str] = []
    succeeded = [item for item in results if item.ok]
    failed = [item for item in results if not item.ok]
    header = f"[{len(succeeded)}/{len(results)} sub-agent task(s) succeeded]"
    lines.append(header)
    for item in sorted(results, key=lambda r: r.index):
        label = f"task {item.index + 1}"
        if item.task.clean_role != DEFAULT_ROLE:
            label += f" ({item.task.clean_role})"
        lines.append("")
        if item.ok:
            lines.append(f"--- {label}: {item.task.goal}")
            lines.append(item.output)
        else:
            lines.append(f"--- {label} FAILED: {item.task.goal}")
            lines.append(f"ERROR: {item.error}")
    if failed:
        lines.append("")
        lines.append(
            f"Note: {len(failed)} task(s) failed. The results above are from the "
            "tasks that did complete — do not assume the failed ones were done."
        )
    if verification:
        lines.append("")
        lines.append("--- verification")
        lines.append(verification)
    elif verified is False:
        lines.append("")
        lines.append(
            "--- verification\nNot verified: the verifier pass could not run, so "
            "treat the results above as unchecked."
        )
    return "\n".join(lines)


def verification_material(results: list[Result], goal: str) -> str:
    """The brief handed to the verifier: the goal, plus what was produced."""
    parts = [f"GOAL:\n{goal.strip()}", "", "OUTPUT TO CHECK:"]
    for item in sorted(results, key=lambda r: r.index):
        if not item.ok:
            continue
        heading = f"[task {item.index + 1}"
        if item.task.clean_role != DEFAULT_ROLE:
            heading += f" / {item.task.clean_role}"
        heading += f"] {item.task.goal}"
        parts.extend([heading, item.output, ""])
    return "\n".join(parts).strip()
