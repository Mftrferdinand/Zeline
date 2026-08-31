"""Structured tool events untuk Zeline App gateway.

Format: setiap event memiliki `event`, `agent_id`, `session_id`, `tool_name`,
`status` (started/running/output/completed/failed), dan payload.
Client dapat render collapsible blocks: Terminal, Web Search, Files.
"""
from __future__ import annotations
from typing import Any

TOOL_EVENT_TYPES = {
    "tool.started",
    "tool.output",
    "tool.completed",
    "tool.failed",
    "agent.message",       # streaming delta dari agent
    "agent.done",          # stream selesai
    "agent.cancelled",
    "session.created",
    "session.closed",
}


def make_tool_event(
    event: str,
    agent_id: str,
    session_id: str,
    *,
    tool_name: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event": event,
        "agent_id": agent_id,
        "session_id": session_id,
        "tool_name": tool_name,
        "status": event.split(".")[-1],  # started / output / completed / failed
        "payload": payload or {},
        "gateway": "zeline_app",
    }


def render_tool_block(tool_name: str, payload: dict[str, Any]) -> str:
    """Render ringkas untuk UI mobile (bisa digunakan client Android/iOS)."""
    name = tool_name or payload.get("tool_name", "tool")
    if name == "run_shell" or name == "execute_code":
        cmd = payload.get("command", payload.get("code", ""))
        snippet = str(cmd)[:120]
        return f"«Terminal\n$ {snippet}\n...»"
    if name == "web_search" or name == "deep_research":
        query = payload.get("query", "")
        return f"«Web Search\n{str(query)[:80]}\n...»"
    if name in ("read_file", "edit_file", "write_file", "patch_file"):
        path = payload.get("path", "")
        return f"«Files\n{str(path).split('/')[-1]}\n...»"
    return f"«{name}\n...»"
