"""Background worker orchestration: non-blocking sub-agents with completion drain.

``delegate_task`` runs sub-agents inline and blocks the turn until they
finish. That is the right shape when the answer is needed now, but a long
independent job (research, a multi-step investigation) should not hold the
conversation hostage. This module adds the other shape:

- ``spawn()`` starts a worker on its own thread and returns a worker id in
  well under a second — the turn never waits.
- Each worker is a full sub-agent (own identity, own tools, own approval
  policy) that runs to completion in the background.
- When a worker finishes or fails, a compact event is queued; the agent
  drains those events into the next turn's ephemeral context, so the model
  learns the outcome without ever polling.

Three details that are easy to get wrong:

- **Approval still has exactly one choke point.** Every tool call a worker
  makes passes through ``ToolExecutor.run()`` — spawning does not bypass
  approval, it only swaps the policy: the interactive picker would hang a
  background thread, so the worker runs under a non-interactive grant
  policy built from the grants declared in the ``spawn_worker`` call. In
  interactive turns the operator approves that exact declaration (the tool
  is Install-class, so it always asks); in grant-policy contexts
  (cron jobs, workers) the declaration must additionally fit inside the
  caller's own grants — enforced by the tool wrapper before ``spawn()``
  runs, so a worker can never exceed its spawner's capability. Anything
  outside the grant is denied, loudly.
- **One worker failing never takes down the rest.** Each worker thread is
  isolated; a crash is captured per worker and the supervisor keeps going.
- **State survives restarts.** The registry is disk-backed (atomic write,
  0600), so a gateway restart does not silently lose track of workers:
  records that were mid-flight are marked interrupted, queued ones resume.
- **Retention is bounded.** Only the last ``MAX_TERMINAL_RECORDS`` (100)
  terminal (done/failed/interrupted) records are kept — completion records
  are the only unbounded growth source, and pruning runs on every persist.
  Only records whose completion event was already delivered are pruned;
  ``running`` and ``queued`` records are never touched by retention.
- **Completion events are delivered exactly once, across restarts.** A
  terminal record carries a persisted ``event_delivered`` flag (False at
  completion, True once ``poll_events()`` hands the event out). On load,
  every terminal record with an undelivered event gets its event
  re-enqueued — a restart never silently drops a completion notice, and a
  second poll or restart never re-emits one already delivered.
- **Workers have a timeout** (default ``WORKER_TIMEOUT_DEFAULT`` = 12
  minutes, overridable per spawn). A worker that runs longer is failed
  with a timeout error, gets its completion event, and frees its slot.
  The clock starts when the worker thread starts (queued time does not
  count); enforcement is lazy — checked on every locked entry point
  (spawn, poll_events, status reads, bind) — so no watchdog thread is
  needed. The hung thread itself is never killed (Python cannot do that
  safely): it is orphaned, and a late return is ignored because the
  record is already settled.
- **Runner context is bound per call, never cached.** ``get_supervisor``
  caches one Supervisor per identity, so the profile/workspace/depth a
  worker runs under is bound explicitly via ``Supervisor.bind()`` before
  every spawn (and refreshed on every agent turn). The default runner
  snapshots that context at call time and refuses to run — loudly — when
  nothing was ever bound. Execution context is never silently ignored or
  inherited from an earlier, unrelated call.

Circular-import rule: this module must NOT import ``zeline.agent`` or
``zeline.tools`` at top level (agent -> tools -> supervisor would cycle).
Those imports happen inside the default task runner, exactly like
``ToolExecutor._spawn_subagent`` does.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from zeline import config

log = logging.getLogger(__name__)

#: Worker lifecycle states.
WORKER_STATUSES = ("queued", "running", "done", "failed", "interrupted", "blocked")

#: Defensive caps: a public gateway must not let one chat fill the owner's disk.
MAX_TASK_CHARS = 4000
MAX_RESULT_CHARS = 2000
MAX_SUMMARY_CHARS = 500
MAX_EVENT_TASK_CHARS = 140

#: Concurrency guard: the model cannot ask for an unbounded thread fan-out.
MAX_WORKERS_HARD_CAP = 500

#: Queue guard: how many workers may wait for a free slot. A full queue
#: makes ``spawn`` fail loudly ("worker queue full") instead of growing the
#: registry — and the thread count — without bound.
MAX_QUEUED = 32

#: How many times a worker run is attempted before it fails loudly.
MAX_ATTEMPTS = 2

#: Retention cap: at most this many TERMINAL (done/failed/interrupted)
#: worker records are kept. Completion records are the only unbounded
#: growth source — ``spawn()`` adds one record per worker and
#: ``_persist_locked()`` writes the whole registry every time — so without
#: retention a busy gateway grows the file (and the in-memory dict)
#: without limit. Pruning runs on every persist: the oldest terminal
#: records beyond the cap are dropped, but ONLY records whose completion
#: event was already delivered (``WorkerRecord.event_delivered``) — a
#: record with a pending event is never pruned, so retention can never
#: eat an undelivered completion notice. ``running``/``queued`` records
#: are never touched by retention.
MAX_TERMINAL_RECORDS = 100

#: Default worker timeout in seconds: a worker that runs longer than this
#: is failed with a timeout error, gets its completion event, and frees
#: its slot for the next queued worker. Overridable per spawn via
#: ``spawn(..., timeout=seconds)``. 12 minutes is long enough for a real
#: research job and short enough that a wedged runner cannot pin the
#: supervisor forever. See ``_check_timeouts_locked`` for the enforcement
#: mechanism (lazy, no watchdog thread; the hung thread is orphaned, never
#: killed).
WORKER_TIMEOUT_DEFAULT = 12 * 60

#: Default grants for a worker: read-only. Broader capability must be declared
#: explicitly in the spawn call — which the operator approves once when
#: allowing the spawn itself.
DEFAULT_WORKER_GRANTS: dict[str, list[str]] = {"tools": [], "risk": ["read"]}

#: ``task_runner(task, grants, worker_identity) -> str``. The extra
#: ``worker_identity`` (beyond the two-argument shape) exists so every worker
#: gets a DISTINCT agent identity: worker sessions build a MemoryStore keyed
#: by identity, and two workers sharing one would corrupt each other's
#: memory file (the same lesson ``zeline.delegation`` documents).
TaskRunner = Callable[[str, dict[str, list[str]], str], str]


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def _truncate(text: Any, limit: int) -> str:
    cleaned = str(text or "")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"


def _verify_result(result: Any, accept_if: str) -> tuple[bool, str]:
    """Check a worker's raw output: (ok, reason).

    Empty output is always rejected — a worker that says nothing did nothing.
    ``accept_if`` is an optional acceptance phrase the result must mention
    (case-insensitive substring); when given and missing, the run is
    rejected so the worker gets one retry to produce what was asked for.
    """
    text = str(result or "").strip()
    if not text:
        return False, "worker returned an empty result"
    needle = str(accept_if or "").strip()
    if needle and needle.casefold() not in text.casefold():
        return False, f"result does not mention the required phrase {needle!r}"
    return True, ""


@dataclass
class WorkerRecord:
    """One background worker's durable state."""

    id: str
    task: str
    status: str = "queued"
    created_at: float = 0.0
    finished_at: float = 0.0
    result: str = ""
    attempts: int = 0
    error: str = ""
    grants: dict[str, list[str]] = field(default_factory=dict)
    #: Acceptance phrase for the verifier. Persisted (not in the original
    #: sketch) because a queued worker resumed after a restart must be
    #: verified against the same phrase — otherwise the retry contract
    #: silently changes mid-flight.
    accept_if: str = ""
    #: When the worker thread actually started executing (0.0 = not yet).
    #: Queued time does NOT count against the timeout — the clock starts
    #: when the run begins, so a worker that waited hours in the queue is
    #: not failed the instant it starts.
    started_at: float = 0.0
    #: Per-worker timeout in seconds (persisted so a record never silently
    #: loses its deadline).
    timeout: float = WORKER_TIMEOUT_DEFAULT
    #: Completion-event delivery flag (persisted). Set to False when the
    #: worker reaches a terminal state; flipped to True by ``poll_events()``
    #: once the event has been handed out. Survives restarts: on load, every
    #: terminal record with an undelivered event gets its event re-enqueued
    #: (exactly-once across restarts), and the retention pruner never
    #: removes a record whose event is still pending.
    event_delivered: bool = True
    #: Worker IDs this worker depends on. It starts only after all
    #: dependencies reach a terminal successful state ("done"). If any
    #: dependency fails, this worker is marked "blocked".
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "attempts": self.attempts,
            "error": self.error,
            "grants": self.grants,
            "accept_if": self.accept_if,
            "started_at": self.started_at,
            "timeout": self.timeout,
            "event_delivered": self.event_delivered,
            "depends_on": self.depends_on,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "WorkerRecord | None":
        if not isinstance(raw, dict):
            return None
        wid = str(raw.get("id") or "")
        if not wid:
            return None
        status = str(raw.get("status") or "queued")
        raw_grants = raw.get("grants")
        if isinstance(raw_grants, dict):
            grants: dict[str, list[str]] = dict(raw_grants)
        else:
            # Missing or malformed persisted grants fail closed to the strict
            # read-only default (documented invariant: a record without a
            # valid declaration never grants more than read). An explicitly
            # empty dict above round-trips as deny-all — a deliberate choice
            # made at spawn time, not a missing declaration.
            grants = {"tools": [], "risk": ["read"]}
        raw_timeout = raw.get("timeout")
        try:
            timeout = float(raw_timeout) if raw_timeout else WORKER_TIMEOUT_DEFAULT
        except (TypeError, ValueError):
            # Corrupt timeout fails closed to the default — never to "no
            # timeout at all".
            timeout = WORKER_TIMEOUT_DEFAULT
        if timeout <= 0 or math.isnan(timeout):
            timeout = WORKER_TIMEOUT_DEFAULT
        try:
            started_at = float(raw.get("started_at") or 0.0)
        except (TypeError, ValueError):
            started_at = 0.0
        # Records written before the delivery flag existed predate
        # restart-proof events: assume already delivered (the old
        # in-memory-only behavior) rather than re-emitting one duplicate
        # event per old terminal record after an upgrade.
        event_delivered = raw.get("event_delivered", True)
        return cls(
            id=wid,
            task=str(raw.get("task") or ""),
            status=status if status in WORKER_STATUSES else "queued",
            created_at=float(raw.get("created_at") or 0.0),
            finished_at=float(raw.get("finished_at") or 0.0),
            result=str(raw.get("result") or ""),
            attempts=int(raw.get("attempts") or 0),
            error=str(raw.get("error") or ""),
            grants=grants,
            accept_if=str(raw.get("accept_if") or ""),
            started_at=started_at,
            timeout=timeout,
            event_delivered=bool(event_delivered),
            depends_on=list(raw.get("depends_on") or []),
        )

    def worker_identity(self, parent_identity: str) -> str:
        """Distinct agent identity for this worker's sub-agent session."""
        suffix = self.id[2:] if self.id.startswith("w_") else self.id
        return f"{parent_identity}::wkr{suffix}"

    def summary(self) -> dict[str, Any]:
        """Compact public view: everything except the full result text."""
        return {
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "attempts": self.attempts,
            "error": self.error,
        }


@dataclass
class WorkerEvent:
    """One completion notice, delivered exactly once via ``poll_events``.

    Exactly-once is enforced by the persisted
    ``WorkerRecord.event_delivered`` flag, not by the in-memory queue
    alone: ``poll_events()`` flips the flag when it hands events out, so
    a restart re-enqueues only events that were never delivered and never
    re-emits one that was.
    """

    worker_id: str
    task: str
    status: str
    summary: str = ""
    error: str = ""
    finished_at: float = 0.0


class Supervisor:
    """Owns a set of background workers for one agent identity.

    All mutable state (registry, event queue, thread handles) is guarded by
    ONE lock. The lock is never held while a worker runs or while joining
    threads — holding it there would deadlock the moment a finishing worker
    tries to record its own completion.
    """

    def __init__(
        self,
        identity: str,
        *,
        profile: str = "full",
        workspace: str = ".",
        depth: int = 0,
        max_workers: int = 4,
        task_runner: TaskRunner | None = None,
    ) -> None:
        self.identity = identity or "cli:local"
        self.profile = profile
        self.workspace = str(workspace or ".")
        self.depth = int(depth)
        self.max_workers = max(1, min(int(max_workers or 4), MAX_WORKERS_HARD_CAP))
        self._task_runner = task_runner
        #: Fail-closed flag for the default runner: only ``bind()`` sets it.
        #: The constructor kwargs above seed the attributes but do NOT count
        #: as a bound context — the default runner refuses to run until a
        #: caller explicitly binds, so a stale or defaulted workspace can
        #: never be used silently.
        self._runner_bound = False
        self._lock = threading.Lock()
        self._records: dict[str, WorkerRecord] = {}
        self._events: list[WorkerEvent] = []
        self._threads: dict[str, threading.Thread] = {}
        #: Steering messages per worker identity (live steering mid-flight).
        #: Not persisted — steering is an interactive operation.
        self._steer_messages: dict[str, list[str]] = {}
        self._shutting_down = False
        self._dir = config.DATA_DIR / "supervisor" / _key(self.identity)
        self._path = self._dir / "workers.json"
        with self._lock:
            self._load_locked()
            # A record left "running" died with the previous process: mark it
            # interrupted (loudly, via an event) instead of pretending it is
            # still alive. Queued records stay queued until a runner can run
            # them (injected runner: resumed below; default runner: resumed
            # on the first bind()).
            now = time.time()
            for record in self._records.values():
                if record.status == "running":
                    record.status = "interrupted"
                    record.finished_at = now
                    record.error = "supervisor restarted while the worker was running"
                    record.event_delivered = False
            # Restart-proof completion events: every terminal record whose
            # event was never delivered gets it re-enqueued now. Records
            # whose event was already polled keep event_delivered=True and
            # are NOT re-emitted — exactly-once across restarts.
            for record in self._records.values():
                if record.status in ("done", "failed", "interrupted") and not record.event_delivered:
                    if record.status == "done":
                        summary = _truncate(record.result, MAX_SUMMARY_CHARS)
                        error = ""
                    else:
                        summary = ""
                        error = record.error
                    self._events.append(
                        WorkerEvent(
                            worker_id=record.id,
                            task=record.task,
                            status=record.status,
                            summary=summary,
                            error=error,
                            finished_at=record.finished_at,
                        )
                    )
            # Persist malas: jangan membuat direktori state untuk identity
            # yang tidak pernah memakai worker. Registry kosong dimuat sama
            # baiknya dari file yang belum ada, jadi tulis hanya bila ada
            # state yang benar-benar berubah (record yang ditandai
            # interrupted, atau completion event yang di-re-enqueue).
            if self._records or self._events:
                self._persist_locked()
            # Queued records resume only when a runner can actually run them:
            # an injected runner is ready immediately, but the default runner
            # has no trustworthy context until bind() — starting it now would
            # just fail loudly with "runner context not bound" and burn both
            # attempts. The first bind() drains the queue with a valid
            # context instead, so approved work is deferred, never destroyed.
            if self._task_runner is not None:
                self._drain_locked()

    # ------------------------------------------------------------------
    # execution-context binding
    # ------------------------------------------------------------------
    def bind(self, *, profile: str, workspace: str, depth: int) -> None:
        """Bind the runner context (profile/workspace/depth), under the lock.

        The Supervisor is cached per identity (see ``get_supervisor``), so
        the context a worker runs under can never be fixed at creation time:
        every caller binds its CURRENT context before spawning, and the
        default runner snapshots it at call time. Re-binding replaces the
        previous values — nothing is ever silently ignored.

        The first bind also resumes queued workers left over from a previous
        process: construction deliberately does not start them when the
        default runner is in use, because it has no trustworthy context yet.

        A bind after ``shutdown()`` is a complete no-op: shutdown is a
        one-way door, and draining here would resurrect queued workers
        after the supervisor promised to stop. Queued work stays queued
        for the NEXT supervisor instance instead of running under a dead one.
        """
        with self._lock:
            if self._shutting_down:
                return
            self._check_timeouts_locked()
            self.profile = str(profile)
            self.workspace = str(workspace or ".")
            self.depth = int(depth)
            self._runner_bound = True
            self._drain_locked()

    def set_task_runner(self, runner: TaskRunner | None) -> None:
        """Replace the task runner (test seam; production uses the default).

        Under the lock, so a worker thread can never observe a half-swapped
        runner.
        """
        with self._lock:
            self._task_runner = runner

    # ------------------------------------------------------------------
    # spawning
    # ------------------------------------------------------------------
    def spawn(
        self,
        task: str,
        *,
        grants: dict[str, Any] | None = None,
        accept_if: str = "",
        timeout: float | None = None,
        depends_on: list[str] | None = None,
    ) -> str:
        """Start a worker; return its id in well under a second.

        ``timeout`` (seconds) overrides ``WORKER_TIMEOUT_DEFAULT`` for this
        worker: a run that exceeds it is failed with a timeout error, gets
        its completion event, and frees its slot. Non-positive values are
        rejected — a timeout of zero would fail the worker instantly and
        "no timeout" is never a safe default to accept silently.

        ``depends_on``: list of worker IDs that must complete successfully
        ("done") before this worker starts. The worker stays "queued" until
        all dependencies are done. If any dependency fails, this worker is
        marked "blocked".

        Raises ``ValueError`` for an empty task, a shut-down supervisor, or
        a full queue — the tool wrapper turns that into an ``"ERROR: ..."``
        string.
        """
        cleaned = _truncate(str(task or "").strip(), MAX_TASK_CHARS)
        if not cleaned:
            raise ValueError("spawn_worker needs a non-empty task.")
        if timeout is None:
            timeout_secs = WORKER_TIMEOUT_DEFAULT
        else:
            timeout_secs = float(timeout)
            if timeout_secs <= 0 or math.isnan(timeout_secs):
                raise ValueError("timeout must be a positive number of seconds.")
        normalized = self._normalize_grants(grants)
        with self._lock:
            self._check_timeouts_locked()
            if self._shutting_down:
                raise ValueError("supervisor is shut down; no new workers accepted.")
            queued = sum(
                1 for item in self._records.values() if item.status == "queued"
            )
            if queued >= MAX_QUEUED:
                raise ValueError(
                    f"worker queue full ({queued} queued, max {MAX_QUEUED})"
                )
            wid = "w_" + uuid.uuid4().hex[:8]
            while wid in self._records:  # collision-proof, not just unlikely
                wid = "w_" + uuid.uuid4().hex[:8]
            running = sum(1 for item in self._records.values() if item.status == "running")
            # Validate dependencies exist.
            dep_ids = [str(d).strip() for d in (depends_on or []) if str(d).strip()]
            for dep_id in dep_ids:
                if dep_id not in self._records:
                    raise ValueError(f"dependency {dep_id!r} not found")
                if dep_id == wid:
                    raise ValueError("worker cannot depend on itself")
            record = WorkerRecord(
                id=wid,
                task=cleaned,
                status="running" if running < self.max_workers else "queued",
                created_at=time.time(),
                grants=normalized,
                accept_if=str(accept_if or "").strip(),
                timeout=timeout_secs,
                depends_on=dep_ids,
            )
            self._records[wid] = record
            self._persist_locked()
            # With dependencies, start as queued — _drain_queue will start it
            # when deps are done.
            if dep_ids:
                record.status = "queued"
                # Drain immediately in case deps are already terminal (H2 fix)
                self._drain_locked()
            elif record.status == "running":
                self._start_thread_locked(wid)
            return wid

    # ------------------------------------------------------------------
    # worker execution
    # ------------------------------------------------------------------
    def _run_worker(self, wid: str) -> None:
        """Thread body: run the task (retry once), verify, record, drain."""
        # Diinisialisasi di luar try supaya blok except terakhir bisa
        # mencatat nilai AKTUAL (bukan MAX_ATTEMPTS) — termasuk 0 bila crash
        # terjadi sebelum loop retry sempat berjalan.
        attempts = 0
        # Telemetri skill: skill apa yang dipakai worker ini + hasil verifikasi.
        # Dicatat di finally (di luar lock); kegagalan telemetri tidak boleh
        # merusak bookkeeping worker.
        # Lazy import: modul ini hanya butuh zeline.config (seperti supervisor),
        # tapi pola file ini memang lazy-import untuk dependensi zeline.
        from zeline import skill_telemetry as _st

        _tele_skills: set[str] = set()
        _tele_ok = False
        _tele_error_kind = ""
        _tele_owner = self.identity
        _tele_started = 0.0
        try:
            with self._lock:
                record = self._records.get(wid)
                if record is None or record.status != "running":
                    return
                # The timeout clock starts NOW — queued time does not count.
                record.started_at = time.time()
                # Deep copy: the runner receives its OWN grants. A shallow
                # copy would share the inner lists with the persisted record,
                # so a runner mutating grants["tools"] would silently corrupt
                # the registry.
                task, grants = record.task, copy.deepcopy(record.grants)
                accept_if = record.accept_if
                runner = self._task_runner or self._default_runner
                _tele_owner = _st.owner_identity(
                    record.worker_identity(self.identity)
                )
                _tele_started = record.started_at
            result_text = ""
            last_error = ""
            last_reason = ""
            ok = False
            # Scope telemetri: skill yang di-load selama worker berjalan
            # teratribusi ke hasil verifikasi worker ini (isolasi per-thread
            # otomatis via ContextVar).
            with _st.usage_scope():
                while attempts < MAX_ATTEMPTS:
                    attempts += 1
                    try:
                        raw = runner(task, grants, record.worker_identity(self.identity))
                        run_error = ""
                    except Exception as exc:  # noqa: BLE001 — a worker crash is data, not a supervisor crash
                        raw = ""
                        run_error = f"{type(exc).__name__}: {exc}".strip() or type(exc).__name__
                    passed, reason = _verify_result(raw, accept_if)
                    if passed:
                        ok, result_text, last_error = True, raw, ""
                        break
                    result_text, last_error, last_reason = raw, run_error, reason
                _tele_skills = set(_st.skills_in_scope())
            with self._lock:
                if ok:
                    error = ""
                else:
                    error = f"gagal setelah {attempts}x percobaan — {last_reason}"
                    if last_error:
                        error += f" (error: {last_error})"
                self._complete_locked(wid, ok=ok, result=result_text, error=error, attempts=attempts)
                _tele_ok = ok
                _tele_error_kind = "" if ok else "verify_failed"
        except Exception as exc:  # noqa: BLE001 — last resort: never leave a record stuck "running"
            with self._lock:
                self._complete_locked(
                    wid,
                    ok=False,
                    result="",
                    error=f"worker thread crashed unexpectedly: {type(exc).__name__}: {exc}",
                    attempts=attempts,
                )
                _tele_ok = False
                _tele_error_kind = "exception:" + type(exc).__name__
        finally:
            with self._lock:
                self._threads.pop(wid, None)
            # Catat outcome telemetri di luar lock; tidak boleh meledak.
            if _tele_skills:
                _tele_duration = (
                    max(0.0, time.time() - _tele_started) if _tele_started else 0.0
                )
                try:
                    for _tele_skill in _tele_skills:
                        _st.record_outcome(
                            _tele_skill,
                            _tele_owner,
                            _tele_ok,
                            duration_s=_tele_duration,
                            error_kind=_tele_error_kind,
                        )
                except Exception:
                    pass

    def _complete_locked(
        self, wid: str, *, ok: bool, result: str, error: str, attempts: int
    ) -> bool:
        """Record a worker's final state, queue its event, start the next queued.

        Must be called with the lock held. Returns False when the record was
        already settled (e.g. shutdown marked it interrupted first) — in that
        case nothing is overwritten and no second event is emitted.
        """
        record = self._records.get(wid)
        if record is None or record.status != "running":
            return False
        now = time.time()
        record.attempts = attempts
        record.finished_at = now
        # A fresh terminal state always means an undelivered event: the
        # matching WorkerEvent is appended just below, and poll_events()
        # flips this flag once the event is handed out.
        record.event_delivered = False
        if ok:
            record.status = "done"
            record.result = _truncate(str(result or "").strip(), MAX_RESULT_CHARS)
            record.error = ""
            summary = _truncate(record.result, MAX_SUMMARY_CHARS)
        else:
            record.status = "failed"
            record.result = ""
            record.error = _truncate(error, MAX_SUMMARY_CHARS)
            summary = ""
        self._events.append(
            WorkerEvent(
                worker_id=wid,
                task=record.task,
                status=record.status,
                summary=summary,
                error=record.error,
                finished_at=now,
            )
        )
        self._threads.pop(wid, None)
        self._persist_locked()
        if not self._shutting_down:
            self._drain_locked()
        return True

    def _check_timeouts_locked(self) -> None:
        """Fail workers that ran past their deadline. Lock must be held.

        Called from every public entry point that takes the lock (spawn,
        poll_events, the status reads, bind): a lightweight LAZY watchdog,
        so no dedicated thread — and its lifecycle — is needed. The agent
        drains completion events on every turn, so a wedged worker is
        noticed no later than the next interaction.

        A timed-out worker is failed via the normal ``_complete_locked``
        path: terminal record, completion event, slot freed for the next
        queued worker. The hung thread itself is NEVER killed (Python
        cannot kill threads safely): its handle is dropped by
        ``_complete_locked`` and, being a daemon, it can never block
        shutdown or process exit. If it ever returns,
        ``_complete_locked`` ignores it because the record is already
        settled — no second event is emitted.
        """
        now = time.time()
        for wid, record in list(self._records.items()):
            if (
                record.status == "running"
                and record.started_at > 0
                and now - record.started_at > record.timeout
            ):
                self._complete_locked(
                    wid,
                    ok=False,
                    result="",
                    error=(
                        "worker timeout: tidak selesai dalam "
                        f"{record.timeout:g} detik"
                    ),
                    attempts=max(record.attempts, 1),
                )

    def _drain_locked(self) -> None:
        """Start queued workers while slots are free. Lock must be held.

        Workers with dependencies start only when all deps are "done".
        If any dep failed/interrupted, the worker is marked "blocked".
        """
        changed = False
        running = sum(1 for item in self._records.values() if item.status == "running")
        for wid, record in self._records.items():  # insertion order = FIFO
            if running >= self.max_workers:
                break
            if record.status != "queued":
                continue
            # Dependency check.
            if record.depends_on:
                dep_statuses = {
                    dep_id: self._records[dep_id].status
                    for dep_id in record.depends_on
                    if dep_id in self._records
                }
                # Any dep failed → blocked.
                if any(s in ("failed", "interrupted", "blocked") for s in dep_statuses.values()):
                    record.status = "blocked"
                    record.error = "blocked: a dependency failed"
                    record.finished_at = time.time()
                    changed = True
                    continue
                # Not all done → wait.
                if not all(s == "done" for s in dep_statuses.values()):
                    continue
            record.status = "running"
            self._start_thread_locked(wid)
            running += 1
            changed = True
        # Persist malas: supervisor untuk identity yang tidak pernah memakai
        # worker tidak boleh meninggalkan direktori state kosong di disk.
        if changed:
            self._persist_locked()

    def _start_thread_locked(self, wid: str) -> None:
        thread = threading.Thread(
            target=self._run_worker, args=(wid,), daemon=True, name=f"zeline-worker-{wid}"
        )
        self._threads[wid] = thread
        thread.start()

    # ------------------------------------------------------------------
    # default task runner: a real sub-agent under a grant policy
    # ------------------------------------------------------------------
    def _default_runner(
        self, task: str, grants: dict[str, list[str]], worker_identity: str
    ) -> str:
        """Run the task with a real sub-agent (the ``TaskRunner`` signature).

        The runner context (profile/workspace/depth) is snapshotted UNDER
        THE LOCK at call time — never captured when the Supervisor was
        created — so a re-``bind`` between two spawns always takes effect
        for the later one. Fail-closed: when ``bind`` was never called
        there is no trustworthy context, so this raises ``RuntimeError``
        ("runner context not bound") instead of silently running in ``"."``
        with the default profile. The worker thread catches it and the
        worker fails loudly, with the reason recorded on its event.

        Every tool call the worker makes still passes the single
        ``ToolExecutor.run()`` choke point — the only change versus an
        interactive turn is the policy: a non-interactive grant policy,
        because the interactive picker would hang a background thread.
        The grants are exactly the ones declared in the ``spawn_worker``
        call, already cleared for this spawn before ``spawn()`` ran
        (operator approval of the exact declaration in interactive turns;
        subset-of-caller enforcement under grant policies). Nothing is
        widened here.
        """
        with self._lock:
            if not self._runner_bound:
                raise RuntimeError(
                    "runner context not bound: call "
                    "bind(profile, workspace, depth) before spawning workers "
                    "with the default runner"
                )
            profile = self.profile
            workspace = self.workspace
            depth = self.depth

        def _run(task: str, task_grants: dict[str, list[str]], _wid: str) -> str:
            # Lazy imports: top-level would cycle (agent -> tools -> supervisor).
            from zeline.agent import Zeline
            from zeline.tools import GrantApprovalPolicy

            policy = GrantApprovalPolicy(
                tools=task_grants.get("tools", []),
                risk_classes=task_grants.get("risk", ["read"]),
            )
            granted_tools = ", ".join(task_grants.get("tools", [])) or "none by name"
            granted_risks = ", ".join(task_grants.get("risk", [])) or "none"
            sub = Zeline(
                identity=_wid,
                tool_profile=profile,
                workspace=workspace,
                system_extra=(
                    "\n\nYou are a WORKER sub-agent spawned to complete ONE focused "
                    "task in the background and report back. You have no memory of "
                    "the parent conversation beyond the brief. Do the work with your "
                    "tools, then reply with a concise, self-contained final summary "
                    "of what you found or did (concrete results, file paths, key "
                    "findings). You run UNATTENDED: nobody can answer questions, so "
                    "`ask_user` is disabled — decide and act on your own. Your "
                    "pre-approved capabilities are: tools "
                    f"[{granted_tools}], risk classes [{granted_risks}]. Tool calls "
                    "outside the grant are denied automatically — work within it."
                ),
                depth=depth + 1,
            )
            return sub.send(task, approval_policy=policy)

        # The identity is fixed per worker record (stable across restarts),
        # but the runner protocol still receives it at call time.
        return _run(task, grants, worker_identity)

    @staticmethod
    def _normalize_grants(grants: Any) -> dict[str, list[str]]:
        """Grants -> canonical ``{"tools": [...], "risk": [...]}``, fail closed.

        Reuses the public ``normalize_job_grants`` shape contract for real
        dicts (unknown risk names dropped, non-lists ignored). Two
        fail-closed rules on top:

        - ``None`` (nothing declared) -> the strict read-only default.
        - A non-dict declaration (a bare string, a list, ...) -> the SAME
          strict read-only default. It must NOT fall through to the
          cron-job floor (read+write): malformed input granting MORE
          capability than declared is a fail-open bug.
        - An explicitly empty dict stays empty (deny-all): a deliberate
          choice, never silently widened to the default.
        """
        if grants is None:
            return {"tools": [], "risk": list(DEFAULT_WORKER_GRANTS["risk"])}
        if not isinstance(grants, dict):
            return {"tools": [], "risk": list(DEFAULT_WORKER_GRANTS["risk"])}
        from zeline.tools import normalize_job_grants  # lazy: avoid the import cycle

        normalized = normalize_job_grants(grants)
        if not normalized["tools"] and not normalized["risk"]:
            # Explicitly empty (or all-garbage) declaration stays empty:
            # fail closed, never silently widened to the default.
            return {"tools": [], "risk": []}
        return normalized

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def poll_events(self) -> list[WorkerEvent]:
        """Return queued completion events and EMPTY the queue (read-once).

        Polling marks each returned event's record ``event_delivered``:
        a restart after a poll never re-emits an event that was already
        handed out (exactly-once across restarts). Delivered records
        become eligible for retention pruning on the next persist.
        """
        with self._lock:
            self._check_timeouts_locked()
            events = self._events
            self._events = []
            delivered = {event.worker_id for event in events}
            changed = False
            for wid in delivered:
                record = self._records.get(wid)
                if record is not None and not record.event_delivered:
                    record.event_delivered = True
                    changed = True
            if changed:
                self._persist_locked()
            return events

    def get_status(self, worker_id: str) -> dict[str, Any] | None:
        """Compact status of one worker (no full result text), or None."""
        with self._lock:
            self._check_timeouts_locked()
            record = self._records.get(str(worker_id or ""))
            return record.summary() if record is not None else None

    def steer_worker(self, worker_id: str, instruction: str) -> bool:
        """Send a steering instruction to a running worker mid-flight.

        The worker picks it up at the start of its next agent-loop iteration —
        no terminate/restart needed . Returns True if the
        worker exists and is running.
        """
        wid = str(worker_id or "")
        instruction = (instruction or "").strip()
        if not wid or not instruction:
            return False
        with self._lock:
            record = self._records.get(wid)
            if record is None or record.status != "running":
                return False
            # Key by worker identity (parent:worker_id) — that's what the
            # agent loop sees as self.identity.
            wident = record.worker_identity(self.identity)
            self._steer_messages.setdefault(wident, []).append(instruction)
            return True

    def pop_steer_messages(self, worker_identity: str) -> list[str]:
        """Take pending steering messages for a worker identity (drain)."""
        with self._lock:
            return self._steer_messages.pop(str(worker_identity or ""), [])

    def get_result(self, worker_id: str) -> dict[str, Any] | None:
        """Full record of one worker (status + result/error), or None."""
        with self._lock:
            self._check_timeouts_locked()
            record = self._records.get(str(worker_id or ""))
            if record is None:
                return None
            data = record.summary()
            data["result"] = record.result
            return data

    def list_workers(self) -> list[dict[str, Any]]:
        """Compact status of all workers, oldest first (no full results)."""
        with self._lock:
            self._check_timeouts_locked()
            return [record.summary() for record in self._records.values()]

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _prune_terminal_locked(self) -> None:
        """Drop the oldest terminal records beyond ``MAX_TERMINAL_RECORDS``.

        Lock must be held. Called from ``_persist_locked`` so retention is
        enforced on every write. NEVER prunes:

        - ``running``/``queued`` records (live work is not retention's
          business), and
        - terminal records whose completion event was not yet delivered
          (``event_delivered`` False) — pruning one would silently eat a
          completion notice the agent has not seen yet. They become
          eligible the moment ``poll_events()`` delivers them.
        """
        terminal = [
            record
            for record in self._records.values()
            if record.status in ("done", "failed", "interrupted")
            and record.event_delivered
        ]
        excess = len(terminal) - MAX_TERMINAL_RECORDS
        if excess <= 0:
            return
        terminal.sort(key=lambda record: record.finished_at)
        for record in terminal[:excess]:
            del self._records[record.id]

    def _persist_locked(self) -> None:
        """Atomic, 0600 write of the registry. Lock must be held."""
        self._prune_terminal_locked()
        temporary = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._dir, 0o700)
            except OSError:
                pass
            # Temp name unique per process+thread: two writers that slipped
            # past the lock (different processes) must not clobber each other
            # before the rename — same pattern as zeline.goals.
            temporary = self._path.with_name(
                f"{self._path.stem}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temporary.write_text(
                json.dumps(
                    {"version": 1, "records": {wid: item.to_dict() for wid, item in self._records.items()}},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            temporary.replace(self._path)
        except OSError as exc:
            # The registry is a convenience, not the authority: in-memory
            # state stays correct and the next persist retries. Never let a
            # disk hiccup fail a spawn or lose a completion — but never
            # swallow it silently either: a persist that keeps failing
            # means restart-proofness is gone.
            log.warning("supervisor persist failed for %s: %s", self.identity, exc)
            if temporary is not None:
                with contextlib.suppress(Exception):
                    temporary.unlink(missing_ok=True)

    def _load_locked(self) -> None:
        """Read the registry; corrupt/missing file -> start empty. Lock held."""
        records: dict[str, WorkerRecord] = {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            items = data.get("records", {}) if isinstance(data, dict) else {}
            if isinstance(items, dict):
                for wid, entry in items.items():
                    try:
                        record = WorkerRecord.from_dict(entry)
                    except (TypeError, ValueError):
                        record = None
                    # One malformed record must not nuke the whole registry.
                    if record is not None and record.id == wid:
                        records[wid] = record
        except (OSError, ValueError):
            records = {}
        self._records = records

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def shutdown(self, timeout: float = 30.0) -> None:
        """Stop accepting work, wait for active workers, mark stragglers.

        The lock is NEVER held while joining: a finishing worker needs the
        lock to record its completion, so join-under-lock would deadlock.
        Workers still alive after the timeout are marked interrupted (with
        an event); queued workers stay queued for the next supervisor.
        Threads are daemons, so a wedged runner can never block exit.
        """
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            threads = list(self._threads.values())
        deadline = time.monotonic() + max(0.0, float(timeout))
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        with self._lock:
            now = time.time()
            for wid, thread in list(self._threads.items()):
                record = self._records.get(wid)
                if record is not None and record.status == "running" and thread.is_alive():
                    record.status = "interrupted"
                    record.finished_at = now
                    record.error = "supervisor shut down before the worker finished"
                    record.event_delivered = False
                    self._events.append(
                        WorkerEvent(
                            worker_id=wid,
                            task=record.task,
                            status="interrupted",
                            error=record.error,
                            finished_at=now,
                        )
                    )
                    self._threads.pop(wid, None)
            self._persist_locked()


_SUPERVISORS: dict[str, Supervisor] = {}
_SUPERVISORS_GUARD = threading.Lock()


def get_supervisor(identity: str) -> Supervisor:
    """Module-level Supervisor registry, one per identity-hash.

    Takes NO keyword arguments on purpose: the execution context
    (profile/workspace/depth) is bound per call via ``Supervisor.bind()``
    and the task runner via ``Supervisor.set_task_runner()``. Passing
    context through a cached lookup meant the first caller's kwargs won and
    every later caller's were silently ignored — the exact bug this shape
    prevents.
    """
    key = _key(identity or "cli:local")
    with _SUPERVISORS_GUARD:
        supervisor = _SUPERVISORS.get(key)
        if supervisor is None:
            supervisor = Supervisor(identity or "cli:local")
            _SUPERVISORS[key] = supervisor
        return supervisor


def peek_supervisor(identity: str) -> Supervisor | None:
    """Return the Supervisor for identity, or None if none exists.

    Does NOT create one — for read-only checks like steering polls where
    creating an empty supervisor would be wasteful.
    """
    key = _key(identity or "cli:local")
    with _SUPERVISORS_GUARD:
        return _SUPERVISORS.get(key)


def drain_completion_block(identity: str) -> str:
    """Drain pending worker events for ``identity`` into one text block.

    Returns ``""`` when there is nothing to report — or when the drain
    itself fails, because a drain failure must never break a turn. The
    block is meant for a turn's ephemeral context: this turn's provider
    payload only, never persisted to history.
    """
    try:
        events = get_supervisor(identity).poll_events()
    except Exception:  # noqa: BLE001 — drain must never break a turn
        return ""
    return format_completions(events)


def format_completions(events: list[WorkerEvent]) -> str:
    """Render completion events as a compact natural-Indonesian block.

    Meant to be injected into a turn's ephemeral context (same lifetime as
    ``turn_extra``): the model reads it this turn, it is never persisted to
    history.
    """
    lines: list[str] = []
    for event in events:
        task = _truncate(event.task, MAX_EVENT_TASK_CHARS)
        label = f"Pekerja {event.worker_id}"
        if event.status == "done":
            summary = event.summary or "(tanpa ringkasan)"
            lines.append(f"{label} selesai: {task} — {summary}")
        elif event.status == "failed":
            reason = event.error or "alasan tidak tercatat"
            lines.append(f"{label} gagal: {task} — {reason}")
        elif event.status == "interrupted":
            reason = event.error or "supervisor berhenti"
            lines.append(f"{label} terhenti: {task} — {reason}")
        else:
            lines.append(f"{label} [{event.status}]: {task}")
    return "\n".join(lines)
