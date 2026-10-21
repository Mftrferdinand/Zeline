"""Interruptible bounded parallel dispatch, without joining abandoned work.

A slot belongs to the running worker, not to the waiting batch. Cancelling a
batch never releases a slot still occupied by foreign blocking code, so repeated
interruptions cannot create an unbounded population of detached workers.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BatchResult:
    results: dict[int, Any] = field(default_factory=dict)
    error: BaseException | None = None
    reason: str | None = None


class ParallelRuntime:
    def __init__(self, limit: int = 5):
        self.limit = min(32, max(1, int(limit)))
        self._slots = threading.BoundedSemaphore(self.limit)

    def run(self, calls: list, invoke: Callable, should_stop: Callable[[], bool],
            deadline: float) -> BatchResult:
        lock = threading.Lock()
        wake = threading.Event()
        stopped = threading.Event()
        completed: dict[int, Any] = {}
        failures: dict[int, BaseException] = {}
        running: set[int] = set()
        next_index = 0

        def cancelled():
            return stopped.is_set() or should_stop() or time.monotonic() >= deadline

        def worker(index, gate):
            try:
                gate.wait()
                if not cancelled():
                    value = invoke(calls[index], cancelled)
                    with lock:
                        completed[index] = value
            except BaseException as exc:
                with lock:
                    failures[index] = exc
            finally:
                with lock:
                    running.discard(index)
                self._slots.release()
                wake.set()

        def snapshot(reason=None):
            with lock:
                error = failures[min(failures)] if failures else None
                return BatchResult(dict(completed), error, reason)

        try:
            while True:
                with lock:
                    failed = bool(failures)
                    active = len(running)
                if failed:
                    # Already admitted peers may hold successful evidence in
                    # audit/trace cleanup. Drain for a bounded grace, never join
                    # foreign blocking work or start another wave after failure.
                    grace = min(deadline, time.monotonic() + .25)
                    while time.monotonic() < grace and not should_stop():
                        with lock:
                            if not running:
                                break
                        wake.wait(min(.02, max(0, grace - time.monotonic())))
                        wake.clear()
                    stopped.set()
                    return snapshot('failed')
                if should_stop():
                    stopped.set()
                    return snapshot('cancelled')
                if time.monotonic() >= deadline:
                    stopped.set()
                    return snapshot('deadline')
                if next_index == len(calls) and not active:
                    return snapshot()
                gate = threading.Event()
                try:
                    # Admit one bounded wave before releasing it. An early
                    # failure must not race away a peer already admitted to this
                    # wave; each peer still reserves its own execution budget.
                    for _ in range(min(self.limit - active, len(calls) - next_index)):
                        if cancelled() or not self._slots.acquire(blocking=False):
                            break
                        index = next_index
                        next_index += 1
                        with lock:
                            running.add(index)
                        try:
                            thread = threading.Thread(target=worker, args=(index, gate), daemon=True,
                                                      name='zeline-tool-worker')
                            thread.start()
                        except BaseException:
                            with lock:
                                running.discard(index)
                            self._slots.release()
                            raise
                finally:
                    gate.set()
                wake.wait(min(.02, max(0, deadline - time.monotonic())))
                wake.clear()
        except BaseException:
            stopped.set()
            raise
