"""One interpretation of native/MCP result envelopes for audit and learning.

A successful tool result is not proof the user's overall task succeeded. Pending
and cancelled work never resolves a previous failure or improves skill scores.
"""
from __future__ import annotations

import contextlib
import json
import re
from typing import Any


def tool_outcome(result: Any) -> str:
    if result is None:
        return "unknown"
    text = str(result).strip()
    if (text.lower() in {"stopped.", "cancelled", "canceled"}
            or re.match(r"(?i)^cancel(?:led|ed):", text)
            or re.match(r"^job=\S+ killed\.", text)):
        return "cancelled"
    if re.match(r"^started background job=\S+", text) or re.match(r"^job=\S+ status=running\b", text):
        return "unknown"
    terminal = re.match(r"^job=\S+ (?:status=exited|already finished) \(exit=(-?\d+)\)", text)
    if terminal:
        return "success" if int(terminal[1]) == 0 else "failure"
    if re.match(r"(?i)^error\b", text):
        return "failure"
    http_status = re.match(r"^Status:\s+(\d{3})\b", text)
    if http_status:
        return "failure" if int(http_status[1]) >= 400 else "success"
    with contextlib.suppress(ValueError, TypeError):
        data = result if isinstance(result, dict) else json.loads(text)
        if isinstance(data, dict):
            if data.get("durable_jobs") or data.get("durable_job"):
                state = data.get("state")
                if state in {"queued", "running"}:
                    return "unknown"
                if state == "cancelled":
                    return "cancelled"
                if state in {"failed", "blocked"}:
                    return "failure"
            if data.get("error") or data.get("success") is False or data.get("ok") is False:
                return "failure"
            for key in ("exit_code", "returncode"):
                if key in data and data[key] is not None and int(data[key]) != 0:
                    return "failure"
    if re.search(r"(?im)^exit(?:[ _]code)?\s*[:=]\s*-?[1-9]\d*\b", text):
        return "failure"
    return "success" if text else "unknown"
