"""Session manager Zeline.

Satu gateway process menangani banyak chat secara concurrent. Store ini:
- mengisolasi identity antar platform (telegram:123 != whatsapp:123),
- memberi lock per session supaya history tidak rusak saat dua request tiba,
- membatasi session aktif memakai LRU supaya bot publik tidak makan RAM tanpa batas.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from zeline import config
from zeline.agent import Zeline


@dataclass
class Session:
    agent: Zeline
    lock: threading.Lock
    last_used: float
    cancel_event: threading.Event = field(default_factory=threading.Event)
    steer_queue: list[str] = field(default_factory=list)
    running: bool = False
    session_id: str = field(default_factory=lambda: f"zel-{uuid.uuid4().hex[:8]}")
    title: str = "New Session"
    created_at: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, max_sessions: int | None = None):
        self.max_sessions = max(1, max_sessions or config.MAX_SESSIONS)
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.RLock()

    def _evict_if_needed(self) -> None:
        while len(self._sessions) >= self.max_sessions:
            self._sessions.popitem(last=False)

    def get_or_create(
        self,
        identity: str,
        tool_profile: str,
        workspace: str | None = None,
        system_extra: str = "",
    ) -> Session:
        with self._lock:
            session = self._sessions.get(identity)
            if session is None:
                self._evict_if_needed()
                session = Session(
                    agent=Zeline(
                        identity=identity,
                        tool_profile=tool_profile,
                        workspace=workspace,
                        system_extra=system_extra,
                    ),
                    lock=threading.Lock(),
                    last_used=time.monotonic(),
                )
                self._sessions[identity] = session
            else:
                self._sessions.move_to_end(identity)
                session.last_used = time.monotonic()
            return session

    def send(
        self,
        identity: str,
        text: str,
        tool_profile: str,
        workspace: str | None = None,
        system_extra: str = "",
        on_tool: Callable | None = None,
    ) -> str:
        session = self.get_or_create(identity, tool_profile, workspace, system_extra)
        # Agent memiliki mutable message history; satu session harus serial.
        with session.lock:
            with self._lock:
                session.cancel_event.clear()
                session.running = True
                if session.title == "New Session":
                    session.title = text.strip().splitlines()[0][:80] or "New Session"

            def take_steer() -> str | None:
                with self._lock:
                    return session.steer_queue.pop(0) if session.steer_queue else None

            try:
                reply = session.agent.send(
                    text,
                    on_tool=on_tool,
                    should_stop=session.cancel_event.is_set,
                    take_steer=take_steer,
                )
                session.last_used = time.monotonic()
                return reply
            finally:
                with self._lock:
                    session.running = False
                    session.steer_queue.clear()

    def stop(self, identity: str) -> bool:
        with self._lock:
            session = self._sessions.get(identity)
            if session is None or not session.running:
                return False
            session.cancel_event.set()
            return True

    def steer(self, identity: str, text: str) -> bool:
        guidance = text.strip()
        with self._lock:
            session = self._sessions.get(identity)
            if session is None or not session.running or not guidance:
                return False
            session.steer_queue.append(guidance)
            return True

    def reset(self, identity: str) -> bool:
        with self._lock:
            session = self._sessions.pop(identity, None)
            if session is not None:
                session.cancel_event.set()
            return session is not None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def status(self, identity: str) -> dict[str, object]:
        with self._lock:
            session = self._sessions.get(identity)
            if session is None:
                return {
                    "session_id": "Not started",
                    "title": "New Session",
                    "created": "—",
                    "last_activity": "—",
                    "model": config.MODEL,
                    "context": "0 messages",
                    "agent_running": False,
                }
            fmt = "%Y-%m-%d %H:%M:%S"
            return {
                "session_id": session.session_id,
                "title": session.title,
                "created": datetime.fromtimestamp(session.created_at).strftime(fmt),
                "last_activity": datetime.fromtimestamp(time.time() - max(0.0, time.monotonic() - session.last_used)).strftime(fmt),
                "model": session.agent.model,
                "context": f"{max(0, len(session.agent.messages) - 1)} messages",
                "agent_running": session.running,
            }


# Alias lama supaya adapter v0.1 bisa dimigrasikan pelan-pelan.
Sessions = SessionStore
