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
from zeline import tools
from zeline.agent import Zeline
from zeline import interaction
from zeline.session_store import SessionPersistence


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
    def __init__(self, max_sessions: int | None = None, persistence: "SessionPersistence | None" = None):
        self.max_sessions = max(1, max_sessions or config.MAX_SESSIONS)
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.RLock()
        # Drain gate: saat di-pause, turn BARU ditolak halus sementara turn yang
        # sedang jalan dibiarkan selesai. Dipakai restart tanpa memotong kerja.
        self._paused = threading.Event()
        # Persistensi disk: history bertahan lintas restart gateway. Bisa
        # dimatikan lewat agent.persist_sessions=false di config.
        if persistence is not None:
            self._persistence = persistence
        elif getattr(config, "PERSIST_SESSIONS", True):
            self._persistence = SessionPersistence()
        else:
            self._persistence = None

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
                agent = Zeline(
                    identity=identity,
                    tool_profile=tool_profile,
                    workspace=workspace,
                    system_extra=system_extra,
                )
                title = "New Session"
                # Pulihkan history dari disk supaya restart gateway tidak
                # menghapus konteks percakapan sebelumnya.
                if self._persistence is not None:
                    try:
                        stored, stored_title = self._persistence.load(identity)
                        if stored:
                            agent.load_history(stored)
                        if stored_title:
                            title = stored_title
                    except Exception:
                        pass
                session = Session(
                    agent=agent,
                    lock=threading.Lock(),
                    last_used=time.monotonic(),
                    title=title,
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
        on_tool_result: Callable | None = None,
        on_iteration: Callable | None = None,
        on_narration: Callable | None = None,
        on_stream_delta: Callable | None = None,
    ) -> str:
        # Gateway sedang drain untuk restart/update: jangan mulai turn baru,
        # dan katakan alasannya. Turn yang sudah jalan tetap diselesaikan.
        if self._paused.is_set():
            return (
                "⏸️ Zeline is finishing current work before restarting. "
                "Send this again in a moment."
            )
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
                    on_tool_result=on_tool_result,
                    on_iteration=on_iteration,
                    should_stop=session.cancel_event.is_set,
                    take_steer=take_steer,
                    on_narration=on_narration,
                    on_stream_delta=on_stream_delta,
                )
                session.last_used = time.monotonic()
                # Simpan history ke disk setelah tiap turn sukses → bertahan
                # lintas restart gateway.
                if self._persistence is not None:
                    try:
                        self._persistence.save(identity, session.agent.export_history(), session.title)
                    except Exception:
                        pass
                    # Arsip permanen: simpan user + assistant turn ini agar bisa
                    # di-recall lintas /new (bukan cuma window aktif). Best-effort.
                    try:
                        self._persistence.append_turn(identity, "user", text, session.title)
                        self._persistence.append_turn(identity, "assistant", reply, session.title)
                    except Exception:
                        pass
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
        # Sebuah tool yang sedang MENUNGGU jawaban ask_user tidak punya proses
        # untuk dibunuh — ia menunggu event. Tanpa ini /stop tidak melepaskan
        # tunggu itu dan sesi terlihat menggantung meski sudah dibatalkan.
        try:
            interaction.cancel(identity)
        except Exception:
            pass
        # /stop harus MEMAKSA berhenti, bukan sekadar menandai flag: perintah
        # foreground yang sedang jalan (pytest/build/install) dibunuh beserta
        # grup prosesnya, jadi turn tidak lagi tertahan sampai perintah selesai.
        try:
            tools.cancel_identity(identity)
        except Exception:
            pass
        return True

    def reflect(self, identity: str, min_tool_calls: int = 4) -> str | None:
        """Jalankan self-improvement review untuk sesi ini (best-effort).

        Dipanggil di akhir sesi penting. Aman: mengembalikan None bila sesi tidak
        ada, terlalu ringan, atau tidak ada yang layak disimpan.
        """
        with self._lock:
            session = self._sessions.get(identity)
        if session is None:
            return None
        with session.lock:
            try:
                return session.agent.reflect(min_tool_calls=min_tool_calls)
            except Exception:
                return None

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
        # /new juga harus melepaskan pertanyaan yang menggantung.
        try:
            interaction.cancel(identity)
        except Exception:
            pass
        # Sama seperti /stop: proses foreground milik sesi ini harus benar-benar
        # dibunuh, bukan dibiarkan hidup setelah sesinya dibuang.
        try:
            tools.cancel_identity(identity)
        except Exception:
            pass
        # /new atau /reset harus menghapus history disk juga, bukan cuma RAM.
        cleared_disk = False
        if self._persistence is not None:
            try:
                cleared_disk = self._persistence.reset(identity)
            except Exception:
                cleared_disk = False
        return session is not None or cleared_disk

    def switch_provider(self, identity: str) -> None:
        """Ganti model/provider aktif untuk sesi ini TANPA menghapus history.

        /model switch harus mengganti "otak" saja — ingatan percakapan (apa yang
        lagi dikerjakan, keputusan sebelumnya) HARUS tetap ada, supaya user tidak
        mengalami amnesia mendadak setelah ganti model. Kalau sesi belum ada di
        RAM tapi ada di disk, biarkan get_or_create memuatnya nanti dengan
        provider baru — history disk tidak disentuh.
        """
        with self._lock:
            session = self._sessions.get(identity)
            if session is not None:
                session.agent.reload_provider()

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ---------------------------------------------------------------- draining
    #
    # Restart yang jujur harus MENUNGGU kerja yang sedang jalan, bukan
    # memotongnya. Tanpa ini `gateway restart` mengirim SIGTERM lalu SIGKILL,
    # jadi build/install/analisis yang sedang berjalan mati di tengah jalan dan
    # user cuma melihat balasan berhenti tanpa penjelasan.

    def pause(self) -> None:
        """Tolak turn BARU; turn yang sedang jalan dibiarkan menyelesaikan."""
        self._paused.set()

    def resume(self) -> None:
        """Terima turn baru lagi (batalkan pause)."""
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def running_identities(self) -> list[str]:
        """Identitas sesi yang turn-nya sedang berjalan saat ini."""
        with self._lock:
            return [key for key, session in self._sessions.items() if session.running]

    def drain(self, timeout: float = 30.0, poll: float = 0.25) -> tuple[bool, list[str]]:
        """Pause lalu tunggu setiap turn aktif selesai.

        Mengembalikan ``(selesai_semua, identitas_yang_masih_jalan)``. Pemanggil
        yang gagal drain sepenuhnya boleh melanjutkan dengan stop paksa, tetapi
        kini bisa MELAPORKAN bahwa ada kerja yang dipotong alih-alih diam.
        """
        self.pause()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            busy = self.running_identities()
            if not busy:
                return True, []
            if time.monotonic() >= deadline:
                return False, busy
            time.sleep(max(0.05, poll))

    def task_snapshot(self, identity: str) -> dict[str, object] | None:
        """Ambil judul dan percakapan terbaru untuk arsip task manual."""
        with self._lock:
            session = self._sessions.get(identity)
            if session is None:
                return None
            messages = [
                str(item.get("content") or "").strip()
                for item in session.agent.messages[-20:]
                if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
            ]
            return {"title": session.title, "messages": messages}

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
