"""Thread-safe dispatch limits shared by a turn and its synchronous children."""
from __future__ import annotations

import threading


class BudgetExceeded(RuntimeError):
    """A configured dispatch allowance is exhausted; do not start more work."""


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
                raise BudgetExceeded(f"{kind} limit reached ({limit})")
            self.used[kind] += 1
