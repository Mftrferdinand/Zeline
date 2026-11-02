"""Thread-safe dispatch limits shared by a turn and its synchronous children."""
from __future__ import annotations

import threading


class BudgetExceeded(RuntimeError):
    """A local dispatch stop, with counts captured at the denied reservation."""

    def __init__(self, message: str, *, kind: str | None = None,
                 used: dict[str, int] | None = None,
                 limits: dict[str, int] | None = None):
        super().__init__(message)
        self.kind = kind
        self.used = dict(used or {})
        self.limits = dict(limits or {})


class RunBudget:
    def __init__(self, provider_requests: int = 0, tool_calls: int = 0):
        for value in (provider_requests, tool_calls):
            if type(value) is not int or not 0 <= value <= 100_000:
                raise BudgetExceeded("limits must be integers from 0 to 100000 (0 disables a limit)")
        self.limits = {"provider_requests": provider_requests, "tool_calls": tool_calls}
        self.used = dict.fromkeys(self.limits, 0)
        self._lock = threading.Lock()

    def consume(self, kind: str) -> None:
        with self._lock:
            limit = self.limits[kind]
            if limit and self.used[kind] >= limit:
                raise BudgetExceeded(f"{kind} limit reached ({limit})", kind=kind,
                                     used=self.used, limits=self.limits)
            self.used[kind] += 1
