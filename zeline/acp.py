"""ACP (Agent Client Protocol) adapter for Zeline.

Implements the ACP JSON-RPC protocol over stdio, allowing IDEs (VS Code,
Zed, JetBrains via ACP plugins) to use Zeline as their AI agent backend.

Protocol: https://agentclientprotocol.com/

Supported methods:
- initialize: handshake, returns agent capabilities
- session/new: create a new session
- session/prompt: send a user prompt, returns response. While the agent
  works, the server streams ``session/update`` notifications
  (``agent_message_chunk`` / ``agent_thought_chunk`` / ``tool_call`` /
  ``tool_call_update``) so IDEs can render progress live. The final
  ``session/prompt`` response shape is unchanged (blocking-compatible).
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
    def __init__(self, out=None):
        self.sessions: dict[str, ACPSession] = {}
        self._lock = threading.Lock()
        # Output stream for JSON-RPC frames (stdout in production,
        # injectable for tests). Guarded by _write_lock so notifications
        # emitted mid-turn never interleave.
        self._out = out if out is not None else sys.stdout
        self._write_lock = threading.Lock()

    def _notify(self, method: str, params: dict) -> bool:
        """Emit a JSON-RPC notification frame (no ``id``).

        Returns False when the client is gone (broken pipe / closed
        stream); callers should stop producing further updates.
        """
        frame = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            line = json.dumps(frame)
        except (TypeError, ValueError):
            return False
        with self._write_lock:
            try:
                self._out.write(line + "\n")
                self._out.flush()
            except (BrokenPipeError, ValueError, OSError):
                return False
        return True

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
            # Jangan bocorkan detail internal (path, dsb) ke klien.
            return self._error(msg_id, -32603, "Internal error")

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
                "version": "0.3.9",
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
        if not text.strip():
            raise ValueError("Empty prompt: nothing to do.")

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
            response = self._run_agent_streaming(session, text)
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

    def _run_agent_streaming(self, session: ACPSession, text: str) -> str:
        """Run the Zeline agent loop, streaming progress as ``session/update``.

        While the turn runs, every model text delta, narration, tool call
        and tool result is emitted as a JSON-RPC notification so the IDE can
        render live progress. The return value and error behavior are
        identical to the old blocking runner: unknown notifications are
        ignored by spec-compliant clients, so this is backward compatible.
        """
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
            sid = session.session_id

            # on_tool / on_tool_result always pair 1:1 in order (agent.py
            # zips parsed_calls with results), so a FIFO queue pairs the
            # toolCallIds correctly, including parallel batches.
            pending_ids: list[str] = []
            seq = [0]

            def _emit(update: dict) -> None:
                if not self._notify("session/update",
                                    {"sessionId": sid, "update": update}):
                    # Client is gone — stop the turn at the next checkpoint
                    # instead of burning tokens nobody will read.
                    session._cancel.set()

            # Batch text deltas: satu flush per N chunk, bukan per chunk.
            # Urutan dijaga: buffer di-flush SEBELUM notifikasi tool/narasi
            # apapun, jadi IDE selalu melihat teks dalam urutan yang benar.
            delta_buf = [""]
            delta_chunks = [0]

            def _flush_deltas() -> None:
                if delta_buf[0]:
                    _emit({"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": delta_buf[0]}})
                    delta_buf[0] = ""
                    delta_chunks[0] = 0

            def on_delta(piece: str) -> None:
                if not piece:
                    return
                delta_buf[0] += piece
                delta_chunks[0] += 1
                if len(delta_buf[0]) >= 4000 or delta_chunks[0] >= 25:
                    _flush_deltas()

            def on_tool(name: str, args: dict) -> None:
                _flush_deltas()  # jaga urutan: teks dulu, baru tool_call
                seq[0] += 1
                tid = f"tc_{seq[0]}"
                pending_ids.append(tid)
                _emit({"sessionUpdate": "tool_call",
                       "toolCallId": tid,
                       "title": str(name),
                       "kind": "other"})

            def on_tool_result(name: str, args: dict, result: str) -> None:
                _flush_deltas()
                tid = pending_ids.pop(0) if pending_ids else f"tc_{seq[0] + 1}"
                _emit({"sessionUpdate": "tool_call_update",
                       "toolCallId": tid,
                       "status": "completed",
                       "content": [{"type": "text",
                                    "text": str(result)[:2000]}]})

            def on_narration(narr: str) -> None:
                if narr:
                    _flush_deltas()
                    _emit({"sessionUpdate": "agent_thought_chunk",
                           "content": {"type": "text", "text": narr}})

            agent = Zeline(
                identity=f"acp:{session.session_id}",
                workspace=session.cwd,
            )
            # Load session history
            for msg in session.history[:-1]:  # exclude current (already added)
                if isinstance(msg, dict) and "role" in msg:
                    agent.messages.append(msg)
            # Run single turn via send(), wired to session cancel (H-2).
            # Callbacks are notifications only; approval behavior is unchanged.
            response = agent.send(
                text,
                should_stop=session._cancel.is_set,
                on_tool=on_tool,
                on_tool_result=on_tool_result,
                on_narration=on_narration,
                on_stream_delta=on_delta,
            )
            _flush_deltas()  # sisa buffer teks di akhir turn
            return response
        except Exception:
            # H-3: let errors propagate honestly to handle() -> -32603
            raise

    def _session_cancel(self, params: dict) -> dict:
        session_id = params.get("sessionId", "")
        with self._lock:
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
        except Exception:
            # Pesan generik seperti handle() — jangan bocorkan detail internal.
            err_id = msg.get("id") if isinstance(msg, dict) else None
            resp = {"jsonrpc": "2.0", "id": err_id,
                    "error": {"code": -32603, "message": "Internal error"}}
        if resp is not None:
            try:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            except BrokenPipeError:
                # L-3 fix: IDE closed pipe, exit cleanly
                break


if __name__ == "__main__":
    main()
