"""Session manager Zeline.

Satu gateway process menangani banyak chat secara concurrent. Store ini:
- mengisolasi identity antar platform (telegram:123 != whatsapp:123),
- memberi lock per session supaya history tidak rusak saat dua request tiba,
- membatasi session aktif memakai LRU supaya bot publik tidak makan RAM tanpa batas.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from zeline import config
from zeline import tasks
from zeline import tools
from zeline.agent import Zeline, ZelineError
from zeline import interaction
from zeline.session_store import SessionPersistence


@dataclass(eq=False)
class TurnTicket:
    """One irreversible cancellation token, allocated before a worker starts."""

    identity: str
    session: "Session"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    admitted_at: float = field(default_factory=time.monotonic)
    started_at: float = 0.0
    iteration: int = 0
    maximum: int = 0
    finished: threading.Event = field(default_factory=threading.Event)
    previous_progress: dict[str, object] | None = None

    def cancel(self, reason: str = "stop") -> None:
        # A hard stop always overrides a prior soft redirect.
        if not self.reason or reason == "stop":
            self.reason = reason
        self.cancel_event.set()


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
    pending_turns: list[TurnTicket] = field(default_factory=list)
    active_turn: TurnTicket | None = None
    wake: threading.Event = field(default_factory=threading.Event)
    cancelling: bool = False
    persistence_error: str = ""


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
        # LRU eviction, but NEVER a session mid-turn. Evicting a running session
        # orphans its worker: /stop and /steer can no longer find it, its
        # history is not persisted, and a second turn for the same identity can
        # start concurrently against the same mutable agent. So we walk from the
        # oldest and evict the first IDLE one; a running session is skipped even
        # if it is the least-recently-used.
        while len(self._sessions) >= self.max_sessions:
            victim = next(
                (key for key, session in self._sessions.items() if not session.running),
                None,
            )
            if victim is None:
                # Every session is busy. Rejecting a new one here would drop the
                # incoming message silently, so we allow a temporary overflow
                # instead — the map shrinks back as soon as any turn finishes.
                return
            self._sessions.pop(victim)

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
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "Session restore failed (%s)", type(exc).__name__)
                        raise ZelineError("Saved conversation could not be restored; "
                                          "existing state has been preserved.") from None
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

    def admit(
        self, identity: str, tool_profile: str, workspace: str | None = None,
        system_extra: str = "", *, interrupt: bool = False,
    ) -> TurnTicket | None:
        """Atomically reserve FIFO order BEFORE starting a gateway worker."""
        with self._lock:
            if self._paused.is_set():
                return None
            session = self.get_or_create(identity, tool_profile, workspace, system_extra)
            previous = self._progress_locked(session)
            ticket = TurnTicket(identity, session, previous_progress=previous)
            if interrupt:
                current = session.active_turn
                if current is None and session.pending_turns:
                    current = session.pending_turns[0]
                if current is not None and not current.cancel_event.is_set():
                    current.cancel("interrupt")
                    session.cancel_event = current.cancel_event
                    # Wake a question waiter. Foreground redirection is handled
                    # by the executor using the ticket's immutable predicate.
                    interaction.cancel(identity)
                    session.agent.force_cancel()
            session.pending_turns.append(ticket)
            session.running = True  # Includes admitted-but-not-yet-started work.
            session.wake.set()
            return ticket

    def release(self, ticket: TurnTicket) -> None:
        """Drop an unused reservation if its transport worker failed to start."""
        with self._lock:
            ticket.cancel()
            session = ticket.session
            if ticket in session.pending_turns:
                session.pending_turns.remove(ticket)
            ticket.finished.set()
            session.running = session.active_turn is not None or bool(session.pending_turns)
            session.wake.set()

    @staticmethod
    def _progress_locked(session: Session) -> dict[str, object] | None:
        ticket = session.active_turn or (session.pending_turns[0] if session.pending_turns else None)
        if ticket is None:
            return None
        return {"elapsed": max(0.0, time.monotonic() - (ticket.started_at or ticket.admitted_at)),
                "iteration": ticket.iteration, "maximum": ticket.maximum or config.MAX_TOOL_ROUNDS,
                "title": session.title, "cancelled": ticket.cancel_event.is_set()}

    def progress(self, identity: str) -> dict[str, object] | None:
        with self._lock:
            session = self._sessions.get(identity)
            return self._progress_locked(session) if session is not None else None

    def wait_turn(self, ticket: TurnTicket) -> bool:
        """Claim the FIFO head; cancellation never requires taking the agent lock."""
        session = ticket.session
        while True:
            with self._lock:
                if (ticket.cancel_event.is_set() or ticket.finished.is_set()
                        or self._sessions.get(ticket.identity) is not session):
                    return False
                if session.active_turn is ticket:
                    return True
                if (not session.cancelling and session.active_turn is None
                        and session.pending_turns and session.pending_turns[0] is ticket):
                    session.pending_turns.pop(0)
                    session.active_turn = ticket
                    session.cancel_event = ticket.cancel_event
                    ticket.started_at = time.monotonic()
                    session.running = True
                    return True
                session.wake.clear()
            session.wake.wait(0.05)

    def finish_turn(self, ticket: TurnTicket) -> None:
        with self._lock:
            session = ticket.session
            if session.active_turn is ticket:
                session.active_turn = None
                session.steer_queue.clear()
            if ticket in session.pending_turns:
                session.pending_turns.remove(ticket)
            ticket.finished.set()
            session.running = session.active_turn is not None or bool(session.pending_turns)
            session.wake.set()

    def send(
        self, identity: str, text: str, tool_profile: str,
        workspace: str | None = None, system_extra: str = "",
        on_tool: Callable | None = None, on_tool_result: Callable | None = None,
        on_iteration: Callable | None = None, on_narration: Callable | None = None,
        on_stream_delta: Callable | None = None, *, ticket: TurnTicket | None = None,
    ) -> str:
        own_ticket = ticket is None
        if ticket is None:
            ticket = self.admit(identity, tool_profile, workspace, system_extra)
        if ticket is None:
            return ("⏸️ Zeline is finishing current work before restarting. "
                    "Send this again in a moment.")
        if ticket.identity != identity:
            raise ValueError("Turn reservation belongs to a different session")
        session = ticket.session
        try:
            if not self.wait_turn(ticket):
                return "Stopped."
            with session.lock:
                if ticket.cancel_event.is_set():
                    return "Stopped."
                with self._lock:
                    if session.title == "New Session":
                        session.title = text.strip().splitlines()[0][:80] or "New Session"

                # Persist the inbound request before provider dispatch. If the
                # process dies before Zeline.send reaches its first internal
                # checkpoint, a later “continue” still sees this request.
                if self._persistence is not None:
                    try:
                        pending = [*session.agent.export_history(), {"role": "user", "content": text}]
                        self._persistence.save_checkpoint(identity, pending, session.title)
                    except Exception as exc:
                        session.persistence_error = f"checkpoint failed ({type(exc).__name__})"
                        logging.getLogger(__name__).warning("Session persistence: %s", session.persistence_error)
                        raise ZelineError("Progress could not be saved; no work was started.") from None

                def take_steer() -> str | None:
                    with self._lock:
                        return session.steer_queue.pop(0) if session.steer_queue else None

                def iteration(current, maximum):
                    with self._lock:
                        ticket.iteration, ticket.maximum = current, maximum
                    if not ticket.cancel_event.is_set() and on_iteration:
                        on_iteration(current, maximum)

                def checkpoint(messages):
                    if self._persistence is None:
                        return
                    try:
                        self._persistence.save_checkpoint(identity, messages, session.title)
                    except Exception as exc:
                        session.persistence_error = f"checkpoint failed ({type(exc).__name__})"
                        logging.getLogger(__name__).warning("Session persistence: %s", session.persistence_error)
                        raise ZelineError("Progress could not be saved; no further tools were started. "
                                          "Verify the last action before retrying.") from None

                reply = session.agent.send(
                    text, on_tool=on_tool, on_tool_result=on_tool_result,
                    on_iteration=iteration, should_stop=ticket.cancel_event.is_set,
                    interrupt_reason=lambda: ticket.reason,
                    take_steer=take_steer, on_narration=on_narration,
                    on_stream_delta=on_stream_delta, on_checkpoint=checkpoint,
                )
                if ticket.cancel_event.is_set():
                    reply = "Stopped."
                session.last_used = time.monotonic()
                if self._persistence is not None:
                    try:
                        self._persistence.save_turn(
                            identity, session.agent.export_history(), text, reply, session.title)
                        session.persistence_error = ""
                    except Exception as exc:
                        # Do not expose exception text, paths, identities or message content.
                        session.persistence_error = f"save_turn failed ({type(exc).__name__})"
                        logging.getLogger(__name__).warning("Session persistence: %s", session.persistence_error)
                        if not ticket.cancel_event.is_set():
                            reply += ("\n\n⚠️ Riwayat percakapan ini gagal disimpan. "
                                      "Hasil tugas tidak dibatalkan; simpan jawaban penting sebelum restart.")
                return reply
        finally:
            # Gateway reservations span result delivery too, so the next turn
            # cannot steal channels or deliver before the prior final is sent.
            if own_ticket:
                self.finish_turn(ticket)

    def stop(self, identity: str) -> bool:
        with self._lock:
            session = self._sessions.get(identity)
            had_work = bool(session is not None and session.running)
            if session is not None:
                session.cancelling = True
                if session.active_turn is not None:
                    session.active_turn.cancel("stop")
                for ticket in session.pending_turns:
                    ticket.cancel("stop")
                session.pending_turns.clear()
                session.cancel_event.set()
                session.steer_queue.clear()
                if session.active_turn is not None or had_work:
                    try:
                        session.agent.force_cancel()
                    except Exception:
                        pass
                session.wake.set()
        # Cancellation does not acquire the mutable-history lock. A fresh turn
        # may be admitted now, but cannot start until owned resources are stopped.
        try:
            interaction.cancel(identity)
            killed = tools.cancel_identity(identity)
        except Exception:
            killed = 0
        finally:
            if session is not None:
                with self._lock:
                    session.cancelling = False
                    session.running = session.active_turn is not None or bool(session.pending_turns)
                    session.wake.set()
        return had_work or bool(killed)

    def reflect(self, identity: str, min_tool_calls: int = 5) -> str | None:
        """Jalankan self-improvement review untuk sesi ini (best-effort).

        Dipanggil di akhir sesi penting. Aman: mengembalikan None bila sesi tidak
        ada, terlalu ringan, atau tidak ada yang layak disimpan. Ambang default
        disamakan dengan ``Zeline.reflect`` (5) supaya keputusan "sesi ini cukup
        berbobot untuk direfleksikan" tidak berbeda tergantung pemanggil.
        """
        with self._lock:
            session = self._sessions.get(identity)
        if session is None:
            return None
        if not session.lock.acquire(blocking=False):
            return None
        ticket = None
        previous_check = getattr(session.agent, "_should_stop", None)
        previous_executor_check = getattr(session.agent.executor, "_should_stop", None)
        try:
            with self._lock:
                if session.running or session.pending_turns or session.cancel_event.is_set():
                    return None
                ticket = TurnTicket(identity, session, maximum=5)
                ticket.started_at = time.monotonic()
                session.active_turn = ticket
                session.running = True
                session.cancel_event = ticket.cancel_event
                session.agent._should_stop = ticket.cancel_event.is_set
                session.agent.executor._should_stop = ticket.cancel_event.is_set
            try:
                result = session.agent.reflect(min_tool_calls=min_tool_calls)
                return None if ticket.cancel_event.is_set() else result
            except Exception:
                return None
        finally:
            if ticket is not None:
                session.agent._should_stop = previous_check
                session.agent.executor._should_stop = previous_executor_check
                self.finish_turn(ticket)
            session.lock.release()

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
                if session.active_turn is not None:
                    session.active_turn.cancel("stop")
                for ticket in session.pending_turns:
                    ticket.cancel("stop")
                session.pending_turns.clear()
                session.wake.set()
                session.cancel_event.set()
                agent = getattr(session, "agent", None)
                if agent is not None and hasattr(agent, "force_cancel"):
                    try:
                        agent.force_cancel()
                    except Exception:
                        pass
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
        # Papan tugas ikut dikosongkan: sesi baru yang membawa task lama adalah
        # polusi yang sama seperti memory yang tidak pernah lupa. Task yang belum
        # selesai diarsipkan lebih dulu ke tasks/cleared.md, jadi hilang dari
        # konteks tapi tidak hilang dari disk.
        try:
            tasks.clear(identity)
        except Exception:
            pass
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
                    "persistence_error": "",
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
                "persistence_error": session.persistence_error,
            }


# Alias lama supaya adapter v0.1 bisa dimigrasikan pelan-pelan.
Sessions = SessionStore
