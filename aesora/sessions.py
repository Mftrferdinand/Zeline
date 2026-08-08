"""Session manager Aesora.

Satu gateway process menangani banyak chat secara concurrent. Store ini:
- mengisolasi identity antar platform (telegram:123 != whatsapp:123),
- memberi lock per session supaya history tidak rusak saat dua request tiba,
- membatasi session aktif memakai LRU supaya bot publik tidak makan RAM tanpa batas.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from aesora import config
from aesora.agent import Aesora


@dataclass
class Session:
    agent: Aesora
    lock: threading.Lock
    last_used: float


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
                    agent=Aesora(
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
            reply = session.agent.send(text, on_tool=on_tool)
            session.last_used = time.monotonic()
            return reply

    def reset(self, identity: str) -> bool:
        with self._lock:
            return self._sessions.pop(identity, None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


# Alias lama supaya adapter v0.1 bisa dimigrasikan pelan-pelan.
Sessions = SessionStore
