"""ACP (Agent Client Protocol) adapter for Zeline.

Implements the ACP JSON-RPC protocol over stdio, allowing IDEs (VS Code,
Zed, JetBrains via ACP plugins) to use Zeline as their AI agent backend.

Protocol: https://agentclientprotocol.com/

Supported methods:
- initialize: handshake, returns agent capabilities
- session/new: create a new session
- session/prompt: send a user prompt, returns response (blocking)
- session/cancel: cancel ongoing prompt
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from typing import Any


class ACPSession:
    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd
        self.history: list[dict] = []
        self._cancel = threading.Event()


class ACPServer:
    def __init__(self):
        self.sessions: dict[str, ACPSession] = {}
        self._lock = threading.Lock()

    def handle(self, msg: dict) -> dict | None:
        # H-1 fix: guard non-dict frames
        if not isinstance(msg, dict):
            return self._error(None, -32600, "Invalid Request: expected object")
        # L-1 fix: validate jsonrpc version
        if msg.get("jsonrpc") != "2.0":
            return self._error(msg.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'")
        method = msg.get("method", "")
        params = msg.get("params", {})
        # M3 fix: "id" absent = notification; id:null explicit = request (must reply)
        is_notification = "id" not in msg
        msg_id = msg.get("id")

        handlers = {
            "initialize": self._initialize,
            "session/new": self._session_new,
            "session/prompt": self._session_prompt,
            "session/cancel": self._session_cancel,
        }
        handler = handlers.get(method)
        if not handler:
            # M-1 fix: don't reply to notifications
            if is_notification:
                return None
            return self._error(msg_id, -32601, f"Method not found: {method}")
        # M-4 fix: validate params is dict
        if not isinstance(params, dict):
            if is_notification:
                return None
            return self._error(msg_id, -32602, "Invalid params: expected object")
        try:
            result = handler(params)
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:
            if is_notification:
                return None
            return self._error(msg_id, -32603, str(exc))

    def _error(self, msg_id: Any, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message},
        }

    def _initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": 1,
            "agentCapabilities": {
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": True,
                },
            },
            "agentInfo": {
                "name": "Zeline",
                "version": "0.3.5",
            },
        }

    def _session_new(self, params: dict) -> dict:
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        cwd = params.get("cwd", "")
        with self._lock:
            # M-3 fix: evict oldest sessions beyond cap to prevent leak
            while len(self.sessions) >= 50:
                oldest = next(iter(self.sessions))
                del self.sessions[oldest]
            self.sessions[session_id] = ACPSession(session_id, cwd)
        return {"sessionId": session_id}

    def _session_prompt(self, params: dict) -> dict:
        session_id = params.get("sessionId", "")
        prompt = params.get("prompt", [])
        # Extract text from prompt blocks
        text_parts = []
        for block in prompt:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype in ("resource", "resource_link"):
                    # M-6: don't silently drop resource blocks
                    uri = block.get("uri", block.get("resource", {}))
                    text_parts.append(f"[attached resource: {uri}]")
        text = "\n".join(text_parts)

        with self._lock:
            session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Unknown session: {session_id}")

        # H-2: clear cancel flag at turn start
        session._cancel.clear()
        # M1 fix: cap history to prevent memory DoS (max 100 msgs, drop oldest)
        # L1 fix: validate length before append
        if len(text) > 16000:
            raise ValueError("Message too long (maximum 16,000 characters).")
        session.history.append({"role": "user", "content": text})
        # Trim old history (keep last 100 messages)
        if len(session.history) > 100:
            session.history = session.history[-100:]

        try:
            response = self._run_agent(session, text)
        except Exception:
            # H-3 fix: don't pollute history with error masquerading as assistant reply
            # Remove the orphaned user message so next turn isn't corrupted
            if session.history and session.history[-1].get("role") == "user":
                session.history.pop()
            raise

        session.history.append({"role": "assistant", "content": response})
        if len(session.history) > 100:
            session.history = session.history[-100:]
        # H-2: report cancelled if flag was set during turn
        if session._cancel.is_set():
            return {"stopReason": "cancelled"}
        return {"stopReason": "end_turn"}

    def _run_agent(self, session: ACPSession, text: str) -> str:
        """Run the Zeline agent loop."""
        try:
            from zeline.agent import Zeline
            from zeline import config as _cfg
            # Check if model is configured
            if not _cfg.MODEL or not _cfg.API_KEY:
                return (
                    f"[Zeline via ACP] No model configured. "
                    f"Run `zeline model` to set up a provider first.\n"
                    f"Session {session.session_id} active in {session.cwd or 'default dir'}."
                )
            agent = Zeline(
                identity=f"acp:{session.session_id}",
                workspace=session.cwd,
            )
            # Load session history
            for msg in session.history[:-1]:  # exclude current (already added)
                if isinstance(msg, dict) and "role" in msg:
                    agent.messages.append(msg)
            # Run single turn via send(), wired to session cancel (H-2)
            response = agent.send(text, should_stop=session._cancel.is_set)
            return response
        except Exception:
            # H-3: let errors propagate honestly to handle() -> -32603
            raise

    def _session_cancel(self, params: dict) -> dict:
        session_id = params.get("sessionId", "")
        session = self.sessions.get(session_id)
        if session:
            session._cancel.set()
        return {}


def main() -> None:
    server = ACPServer()
    # M2 fix: cap line length to prevent memory DoS via no-newline flood
    MAX_LINE = 10 * 1024 * 1024  # 10MB max per line
    while True:
        line = sys.stdin.readline(MAX_LINE + 1)
        if not line:
            break
        if len(line) > MAX_LINE:
            # Line too long, consume until newline then error
            while line and not line.endswith("\n"):
                line = sys.stdin.readline(MAX_LINE + 1)
            err = {"jsonrpc": "2.0", "id": None,
                   "error": {"code": -32700, "message": "Parse error: line too long"}}
            try:
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                break
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # M-2 fix: respond with -32700 Parse error instead of silent hang
            err = {"jsonrpc": "2.0", "id": None,
                   "error": {"code": -32700, "message": "Parse error"}}
            try:
                sys.stdout.write(json.dumps(err) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                break
            continue
        try:
            # H-1 fix: never let one bad frame kill the process
            resp = server.handle(msg)
        except Exception as exc:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32603, "message": f"Internal error: {exc}"}}
        if resp is not None:
            try:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                # L-3 fix: IDE closed pipe, exit cleanly
                break


if __name__ == "__main__":
    main()
