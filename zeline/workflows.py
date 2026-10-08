"""Visual workflow builder backend (OpenHuman parity).

Workflows are DAGs of nodes stored as JSON in ~/.zeline/workflows/.
Node types:
- task: run a prompt/tool via the agent
- approval: pause for human approval (gate)
- condition: branch on a simple expression (future)

The desktop app renders these visually; this module handles
storage, validation, and execution.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _wf_dir() -> Path:
    d = Path.home() / ".zeline" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def list_workflows() -> list[dict]:
    result = []
    for jf in sorted(_wf_dir().glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            result.append({
                "id": jf.stem,
                "name": data.get("name", jf.stem),
                "nodes": len(data.get("nodes", [])),
                "updated": data.get("updated", ""),
            })
        except Exception:
            continue
    return result


def _sanitize_wf_id(wf_id: str) -> str:
    """Sanitize workflow ID to prevent path traversal."""
    return "".join(c for c in wf_id if c.isalnum() or c in "_-")[:64]


def get_workflow(wf_id: str) -> dict | None:
    wid = _sanitize_wf_id(wf_id)
    if not wid:
        return None
    p = _wf_dir() / f"{wid}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_workflow(wf_id: str | None, name: str, nodes: list,
                  edges: list) -> str:
    """Save a workflow. Returns the workflow ID."""
    # Consistent type validation up front: everything is ValueError.
    if not isinstance(nodes, list):
        raise ValueError(f"nodes must be a list, got {type(nodes).__name__}")
    if not isinstance(edges, list):
        raise ValueError(f"edges must be a list, got {type(edges).__name__}")
    for n in nodes:
        if not isinstance(n, dict):
            raise ValueError(f"node must be a dict, got {type(n).__name__}: {n!r}")
    # Validate nodes
    valid_types = {"task", "approval", "note"}
    seen_ids: set[str] = set()
    for n in nodes:
        if n.get("type") not in valid_types:
            raise ValueError(f"Invalid node type: {n.get('type')}")
        if not n.get("id"):
            raise ValueError("Node missing id")
        nid = str(n["id"])
        if nid in seen_ids:
            raise ValueError(f"duplicate node id: {nid!r}")
        seen_ids.add(nid)
    # Validate edges reference existing nodes
    node_ids = {n["id"] for n in nodes}
    for e in edges:
        if e.get("from") not in node_ids or e.get("to") not in node_ids:
            raise ValueError(f"Edge references unknown node: {e}")
    wid = wf_id or f"wf_{uuid.uuid4().hex[:8]}"
    # Sanitize ID
    wid = "".join(c for c in wid if c.isalnum() or c in "_-")[:64] or f"wf_{uuid.uuid4().hex[:8]}"
    data = {
        "id": wid,
        "name": name,
        "nodes": nodes,
        "edges": edges,
        "updated": datetime.now(timezone.utc).isoformat(),  # A2-L8: tz-aware
    }
    p = _wf_dir() / f"{wid}.json"
    # Atomic write: tmp + os.replace so a crash mid-write can't leave a
    # corrupt half-written JSON (same pattern as _persist_locked).
    tmp = p.with_name(f"{wid}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return wid


def delete_workflow(wf_id: str) -> bool:
    wid = _sanitize_wf_id(wf_id)
    if not wid:
        return False
    p = _wf_dir() / f"{wid}.json"
    if p.is_file():
        p.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------
# Runs a saved workflow DAG: task nodes execute as agent turns, approval
# nodes pause for a human decision, note nodes are documentation.
#
# ``execute_workflow`` is non-blocking: it validates, creates an execution
# record, spawns a daemon thread and returns an exec_id immediately.
# ``pause_workflow`` / ``resume_workflow`` control a live execution.
# State is mirrored to ~/.zeline/workflows/executions/<exec_id>.json
# after every transition (atomic write), so a dashboard can poll progress
# and a restart never resurrects a half-run thread as "running".

WORKFLOW_STATUSES = (
    "running", "waiting_approval", "paused", "done", "failed", "cancelled",
    "interrupted",
)
NODE_STATUSES = (
    "pending", "running", "waiting_approval", "done", "failed",
    "cancelled", "skipped",
)
_TERMINAL = {"done", "failed", "cancelled"}

#: Max in-memory executions kept; older terminal entries are evicted.
#: Evicted entries stay readable via get_execution()'s disk fallback.
_MAX_KEPT_EXECUTIONS = 100

_EXECUTIONS: dict[str, dict] = {}
_EXEC_RUNTIME: dict[str, dict] = {}
_EXEC_LOCK = threading.Lock()


def _evict_old_executions_locked() -> None:
    """Evict oldest terminal executions beyond the keep limit.

    Must be called with _EXEC_LOCK held. Only terminal entries
    (done/failed/cancelled/interrupted) are evicted — live executions
    are never touched, so pause/resume/approval gates keep working.
    """
    if len(_EXECUTIONS) <= _MAX_KEPT_EXECUTIONS:
        return
    terminal = ("done", "failed", "cancelled", "interrupted")
    evictable = [eid for eid, ex in _EXECUTIONS.items()
                 if ex.get("status") in terminal]
    excess = len(_EXECUTIONS) - _MAX_KEPT_EXECUTIONS
    for eid in evictable[:max(0, excess)]:
        _EXECUTIONS.pop(eid, None)


def _exec_dir() -> Path:
    d = _wf_dir() / "executions"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def _sanitize_exec_id(exec_id: str) -> str:
    return "".join(c for c in exec_id if c.isalnum() or c in "_-")[:64]


def _topo_sort(nodes: list[dict], edges: list[dict]) -> list[str]:
    """Kahn's algorithm. Returns node ids in execution order.

    Raises ValueError if the graph has a cycle (would loop forever).
    """
    node_ids = [n["id"] for n in nodes]
    indeg: dict[str, int] = {nid: 0 for nid in node_ids}
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        src, dst = e.get("from"), e.get("to")
        if src in adj and dst in indeg and dst not in adj[src]:
            adj[src].append(dst)
            indeg[dst] += 1
    queue = [nid for nid in node_ids if indeg[nid] == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for nxt in adj[nid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(node_ids):
        raise ValueError("workflow has a cycle; refusing to execute (would loop forever)")
    return order


def _new_execution(wf: dict) -> dict:
    now = time.time()
    nodes_state = {}
    for n in wf["nodes"]:
        nodes_state[n["id"]] = {
            "id": n["id"],
            "type": n.get("type"),
            "label": n.get("label", ""),
            "status": "pending",
            "result": "",
            "error": "",
            "started_at": 0.0,
            "ended_at": 0.0,
        }
    return {
        "exec_id": f"exec_{uuid.uuid4().hex[:8]}",
        "wf_id": wf["id"],
        "wf_name": wf.get("name", wf["id"]),
        "status": "running",
        "started_at": now,
        "ended_at": 0.0,
        "nodes": nodes_state,
        "log": [_log_event("started", "", f"workflow '{wf.get('name', wf['id'])}' started")],
    }


def _log_event(event: str, node: str, detail: str = "") -> dict:
    return {"ts": time.time(), "event": event, "node": node, "detail": detail[:500]}


def _persist_locked(exec_id: str) -> None:
    """Write execution JSON atomically. Caller must hold _EXEC_LOCK."""
    ex = _EXECUTIONS.get(exec_id)
    if ex is None:
        return
    p = _exec_dir() / f"{_sanitize_exec_id(exec_id)}.json"
    tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(ex, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _append_log_locked(exec_id: str, event: str, node: str, detail: str = "") -> None:
    ex = _EXECUTIONS.get(exec_id)
    if ex is not None:
        ex["log"].append(_log_event(event, node, detail))


def execute_workflow(
    wf_id: str,
    agent,
    *,
    node_timeout: float = 300.0,
    approval_timeout: float = 1800.0,
    on_approval=None,
) -> str:
    """Start executing a workflow in a background thread.

    ``agent`` is duck-typed: anything with ``.send(text) -> str``.
    Returns the execution id immediately; poll :func:`get_execution`.

    Raises ValueError for unknown workflow, bad timeouts, or cycles.
    """
    wf = get_workflow(wf_id)
    if wf is None:
        raise ValueError(f"unknown workflow: {wf_id!r}")
    try:
        node_timeout = float(node_timeout)
        approval_timeout = float(approval_timeout)
    except (TypeError, ValueError):
        raise ValueError("timeouts must be numbers of seconds")
    if node_timeout <= 0 or approval_timeout <= 0:
        raise ValueError("timeouts must be positive")
    nodes = wf.get("nodes", [])
    edges = wf.get("edges", [])
    if not nodes:
        raise ValueError("workflow has no nodes")
    order = _topo_sort(nodes, edges)  # raises on cycle
    node_map = {n["id"]: n for n in nodes}

    ex = _new_execution(wf)
    exec_id = ex["exec_id"]
    rt = {
        "approval_event": threading.Event(),
        "approved": None,
        "paused": False,
        "stop": False,
        "thread": None,
    }
    with _EXEC_LOCK:
        _EXECUTIONS[exec_id] = ex
        _EXEC_RUNTIME[exec_id] = rt
        _persist_locked(exec_id)
    t = threading.Thread(
        target=_run_execution,
        args=(exec_id, agent, node_map, order, node_timeout, approval_timeout, on_approval),
        name=f"wf-exec-{exec_id}",
        daemon=True,
    )
    rt["thread"] = t
    t.start()
    return exec_id


def _run_task_node(agent, prompt: str, timeout: float) -> tuple[bool, str]:
    """Run agent.send in a worker thread so a hung turn can't hang the workflow.

    Returns (ok, result_or_error). The worker thread is a daemon: on timeout
    we abandon it (CPython can't kill threads) and fail the node loudly.
    """
    box: dict[str, str] = {}

    def _target() -> None:
        try:
            box["result"] = agent.send(prompt)
        except Exception as exc:  # noqa: BLE001 - surfaced as node failure
            box["error"] = f"{type(exc).__name__}: {exc}"

    th = threading.Thread(target=_target, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return False, f"timed out after {timeout:g}s"
    if "error" in box:
        return False, box["error"][:2000]
    return True, str(box.get("result", ""))[:8000]


def _run_execution(exec_id, agent, node_map, order, node_timeout, approval_timeout, on_approval) -> None:
    def _set_node(nid: str, **kw) -> None:
        with _EXEC_LOCK:
            ex = _EXECUTIONS.get(exec_id)
            if ex is None:
                return
            ex["nodes"][nid].update(kw)
            _persist_locked(exec_id)

    def _set_wf(status: str, event: str, node: str = "", detail: str = "") -> None:
        with _EXEC_LOCK:
            ex = _EXECUTIONS.get(exec_id)
            if ex is None:
                return
            ex["status"] = status
            if event:
                ex["log"].append(_log_event(event, node, detail))
            if status in _TERMINAL:
                ex["ended_at"] = time.time()
                _evict_old_executions_locked()
            _persist_locked(exec_id)

    def _should_stop() -> bool:
        with _EXEC_LOCK:
            rt = _EXEC_RUNTIME.get(exec_id)
            return bool(rt and rt["stop"])

    def _wait_while_paused() -> bool:
        """Block while paused. Returns False if asked to stop."""
        while True:
            with _EXEC_LOCK:
                rt = _EXEC_RUNTIME.get(exec_id)
                if not rt or rt["stop"]:
                    return False
                paused = rt["paused"]
            if not paused:
                return True
            time.sleep(0.2)

    try:
        for nid in order:
            if _should_stop():
                break
            if not _wait_while_paused():
                break
            node = node_map[nid]
            ntype = node.get("type")
            prompt = str(node.get("prompt") or node.get("label") or "").strip()

            if ntype == "note":
                _set_node(nid, status="skipped", ended_at=time.time(),
                          result="documentation only")
                with _EXEC_LOCK:
                    _append_log_locked(exec_id, "node_skipped", nid, "note node")
                    _persist_locked(exec_id)
                continue

            if ntype == "approval":
                _set_node(nid, status="waiting_approval", started_at=time.time())
                _set_wf("waiting_approval", "approval_requested", nid, prompt[:200])
                if on_approval is not None:
                    try:
                        on_approval(exec_id, nid, prompt)
                    except Exception:  # noqa: BLE001 - callback must not kill execution
                        pass
                with _EXEC_LOCK:
                    rt = _EXEC_RUNTIME.get(exec_id)
                    ev = rt["approval_event"] if rt else None
                    if rt:
                        rt["approved"] = None
                        ev.clear()
                granted = ev.wait(timeout=approval_timeout) if ev is not None else False
                with _EXEC_LOCK:
                    rt = _EXEC_RUNTIME.get(exec_id)
                    # W4 fix: honor a decision that landed after wait() returned.
                    # resume_workflow() sets rt["approved"] BEFORE setting the
                    # event, so a non-None value here means the operator decided
                    # in the microsecond race window between wait() returning
                    # False (timeout) and this thread marking the node failed.
                    approved = rt["approved"] if rt is not None else None
                    if not granted and approved is not None:
                        granted = True  # late operator decision wins over timeout
                if not granted:
                    _set_node(nid, status="failed", ended_at=time.time(),
                              error=f"approval timed out after {approval_timeout:g}s")
                    _set_wf("failed", "approval_timeout", nid)
                    return
                if not approved:
                    _set_node(nid, status="cancelled", ended_at=time.time(),
                              result="denied by operator")
                    _set_wf("cancelled", "approval_denied", nid)
                    return
                _set_node(nid, status="done", ended_at=time.time(), result="approved")
                _set_wf("running", "approval_granted", nid)
                continue

            if ntype == "task":
                if not prompt:
                    _set_node(nid, status="failed", ended_at=time.time(),
                              error="task node has no prompt")
                    _set_wf("failed", "node_failed", nid, "empty prompt")
                    return
                _set_node(nid, status="running", started_at=time.time())
                with _EXEC_LOCK:
                    _append_log_locked(exec_id, "node_started", nid, prompt[:200])
                    _persist_locked(exec_id)
                ok, out = _run_task_node(agent, prompt, node_timeout)
                if not ok:
                    _set_node(nid, status="failed", ended_at=time.time(), error=out)
                    _set_wf("failed", "node_failed", nid, out[:200])
                    return
                _set_node(nid, status="done", ended_at=time.time(), result=out)
                with _EXEC_LOCK:
                    _append_log_locked(exec_id, "node_done", nid)
                    _persist_locked(exec_id)
                continue

            # Unknown type (shouldn't happen post-validation, but fail closed)
            _set_node(nid, status="failed", ended_at=time.time(),
                      error=f"unknown node type: {ntype!r}")
            _set_wf("failed", "node_failed", nid, f"unknown type {ntype!r}")
            return

        with _EXEC_LOCK:
            ex = _EXECUTIONS.get(exec_id)
            rt = _EXEC_RUNTIME.get(exec_id)
            if ex is not None and ex["status"] not in _TERMINAL and not (rt and rt["stop"]):
                ex["status"] = "done"
                ex["ended_at"] = time.time()
                ex["log"].append(_log_event("finished", "", "all nodes done"))
                _persist_locked(exec_id)
    except Exception as exc:  # noqa: BLE001 - never let the thread die silently
        _set_wf("failed", "engine_error", "", f"{type(exc).__name__}: {exc}"[:500])
    finally:
        # Don't leak runtime entries for finished executions.
        with _EXEC_LOCK:
            _EXEC_RUNTIME.pop(exec_id, None)


def _copy_execution(ex: dict) -> dict:
    return json.loads(json.dumps(ex))


def _reconcile_stale(data: dict) -> dict:
    """Rewrite a stale non-terminal status as "interrupted".

    After a process restart the in-memory runtime (_EXECUTIONS/_EXEC_RUNTIME)
    is empty, so a persisted "running"/"paused"/"waiting_approval" with no
    ended_at can never progress — its thread is gone. Returning the stale
    status would mislead dashboards into showing a live execution.
    """
    if data.get("status") not in _TERMINAL and not data.get("ended_at"):
        data["status"] = "interrupted"
        for n in data.get("nodes", {}).values():
            if isinstance(n, dict) and n.get("status") in (
                "pending", "running", "waiting_approval",
            ):
                n["status"] = "interrupted"
    return data


def get_execution(exec_id: str) -> dict | None:
    """Return a snapshot of an execution (in-memory first, else disk)."""
    eid = _sanitize_exec_id(exec_id)
    if not eid:
        return None
    with _EXEC_LOCK:
        ex = _EXECUTIONS.get(eid)
        if ex is not None:
            return _copy_execution(ex)
    # No live runtime entry: the thread is gone (e.g. process restarted).
    # Reconcile any stale non-terminal status so callers see the truth.
    p = _exec_dir() / f"{eid}.json"
    if not p.is_file() or p.is_symlink():
        return None
    try:
        return _reconcile_stale(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_executions(wf_id: str | None = None) -> list[dict]:
    """List executions (newest first), optionally filtered by workflow."""
    out = []
    try:
        files = sorted(_exec_dir().glob("exec_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    with _EXEC_LOCK:
        live = set(_EXECUTIONS.keys())
    for p in files:
        if p.is_symlink():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if wf_id is not None and data.get("wf_id") != wf_id:
            continue
        # Only reconcile stale status when the execution has no live thread.
        if data.get("exec_id") not in live:
            data = _reconcile_stale(data)
        out.append({
            "exec_id": data.get("exec_id", p.stem),
            "wf_id": data.get("wf_id", ""),
            "wf_name": data.get("wf_name", ""),
            "status": data.get("status", "unknown"),
            "started_at": data.get("started_at", 0.0),
            "ended_at": data.get("ended_at", 0.0),
            "nodes": len(data.get("nodes", {})),
        })
    return out


def pause_workflow(exec_id: str) -> bool:
    """Pause a running execution between nodes. Returns False if not pausable."""
    eid = _sanitize_exec_id(exec_id)
    with _EXEC_LOCK:
        rt = _EXEC_RUNTIME.get(eid)
        ex = _EXECUTIONS.get(eid)
        if rt is None or ex is None:
            return False
        if ex["status"] != "running":
            return False
        rt["paused"] = True
        ex["status"] = "paused"
        ex["log"].append(_log_event("paused", "", "paused by operator"))
        _persist_locked(eid)
        return True


def resume_workflow(exec_id: str, approved: bool = True) -> bool:
    """Resume a paused execution, or resolve a waiting approval gate.

    For ``waiting_approval``: ``approved=True`` continues, ``False`` cancels.
    For ``paused``: clears the pause (``approved`` ignored).
    Returns False if the execution is not in a resumable state.
    """
    eid = _sanitize_exec_id(exec_id)
    with _EXEC_LOCK:
        rt = _EXEC_RUNTIME.get(eid)
        ex = _EXECUTIONS.get(eid)
        if rt is None or ex is None:
            return False
        status = ex["status"]
        if status == "waiting_approval":
            rt["approved"] = bool(approved)
            ex["log"].append(_log_event(
                "approval_resolved", "",
                "approved" if approved else "denied"))
            rt["approval_event"].set()
            _persist_locked(eid)
            return True
        if status == "paused":
            rt["paused"] = False
            ex["status"] = "running"
            ex["log"].append(_log_event("resumed", "", "resumed by operator"))
            _persist_locked(eid)
            return True
        return False


def cancel_workflow(exec_id: str) -> bool:
    """Cancel a live execution. Returns False if already terminal/missing."""
    eid = _sanitize_exec_id(exec_id)
    with _EXEC_LOCK:
        rt = _EXEC_RUNTIME.get(eid)
        ex = _EXECUTIONS.get(eid)
        if rt is None or ex is None:
            return False
        if ex["status"] in _TERMINAL:
            return False
        rt["stop"] = True
        rt["paused"] = False
        rt["approved"] = False
        rt["approval_event"].set()
        ex["status"] = "cancelled"
        ex["ended_at"] = time.time()
        ex["log"].append(_log_event("cancelled", "", "cancelled by operator"))
        _evict_old_executions_locked()
        _persist_locked(eid)
        return True
