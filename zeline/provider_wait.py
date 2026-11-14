"""Bound provider response silence independently of productive task runtime."""
from contextvars import ContextVar
from functools import wraps
import math
import time
from zeline import config

_CURRENT = ContextVar('zeline_provider_wait', default=None)
DEFAULT_STALL_SECONDS = 90.0


class _Wait:
    def __init__(self, seconds):
        self.seconds = seconds
        self.last_progress = time.monotonic()

    def expired(self):
        return time.monotonic() - self.last_progress >= self.seconds


def expired():
    current = _CURRENT.get()
    return current is not None and current.expired()


def progress():
    current = _CURRENT.get()
    if current is not None:
        current.last_progress = time.monotonic()


def bounded(call):
    @wraps(call)
    def wrapped(self, *args, **kwargs):
        if _CURRENT.get() is not None:
            return call(self, *args, **kwargs)
        try:
            seconds = float(getattr(config, 'PROVIDER_STALL_SECONDS', DEFAULT_STALL_SECONDS))
        except (TypeError, ValueError):
            seconds = DEFAULT_STALL_SECONDS
        if not math.isfinite(seconds) or seconds <= 0:
            seconds = DEFAULT_STALL_SECONDS
        token = _CURRENT.set(_Wait(seconds))
        try:
            return call(self, *args, **kwargs)
        finally:
            _CURRENT.reset(token)
    return wrapped
