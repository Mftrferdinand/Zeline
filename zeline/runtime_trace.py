"""Small private rotating tool-boundary trace, without commands or payloads.

Mutation provenance stays in events.db. This separate operational trace records
before execution so an interrupted read/search is visible even when no tool
result or final transcript can be saved. Recording is strictly best-effort.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from zeline import config

MAX_TRACE_BYTES = 1_048_576
_TRACE_LOCK = threading.Lock()


def trace_path() -> Path:
    return config.DATA_DIR / "logs" / "runtime-trace.jsonl"


def _resources() -> dict[str, int]:
    values = {"pid": os.getpid(), "thread_id": threading.get_ident()}
    # Bounded Linux/Android sample; other platforms simply omit these fields.
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as stream:
            status = stream.read(65_536)
        for field, key in (("VmRSS", "rss_kib"), ("VmHWM", "peak_rss_kib"), ("Threads", "threads")):
            match = re.search(rf"^{field}:\s+(\d+)", status, re.MULTILINE)
            if match:
                values[key] = int(match.group(1))
    except OSError:
        pass
    return values


def _append(record: dict) -> None:
    payload = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    path = trace_path()
    with _TRACE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(payload) > MAX_TRACE_BYTES:
            older = path.with_name(path.name + ".1")
            oldest = path.with_name(path.name + ".2")
            oldest.unlink(missing_ok=True)
            if older.exists():
                older.replace(oldest)
            path.replace(older)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            # Each bounded record is written synchronously before work starts.
            os.write(fd, payload)
        finally:
            os.close(fd)


def _record(record: dict) -> None:
    try:
        _append({**record, **_resources(), "ts": time.time()})
    except Exception:
        # Diagnostics cannot turn a successful tool into an error.
        pass


@contextlib.contextmanager
def tool_span(identity: str, tool: str):
    """Record an invocation without accepting or inspecting its arguments."""
    base = {
        "call_id": uuid.uuid4().hex,
        "owner": hashlib.sha256(str(identity).encode()).hexdigest()[:32],
        "tool": re.sub(r"[^a-zA-Z0-9_-]", "_", str(tool))[:96],
    }
    started = time.monotonic()
    _record({**base, "phase": "start"})
    try:
        yield
    except BaseException as exc:
        _record({**base, "phase": "end", "outcome": "raised",
                 "exception_type": type(exc).__name__,
                 "elapsed_seconds": round(time.monotonic() - started, 6)})
        raise
    else:
        _record({**base, "phase": "end", "outcome": "completed",
                 "elapsed_seconds": round(time.monotonic() - started, 6)})
