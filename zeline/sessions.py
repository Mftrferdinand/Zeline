"""Session manager Zeline.

Satu gateway process menangani banyak chat secara concurrent. Store ini:
- mengisolasi identity antar platform (telegram:123 != whatsapp:123),
- memberi lock per session supaya history tidak rusak saat dua request tiba,
- membatasi session aktif memakai LRU supaya bot publik tidak makan RAM tanpa batas.
"""
from __future__ import annotations

import re
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
    # Progres turn yang sedang berjalan — dipakai banner interupsi ("⚡
    # Interrupting current task, iteration N/M, X elapsed") saat pesan mendesak
    # datang di tengah kerja. Diperbarui lewat on_iteration.
    turn_started: float = 0.0
    current_iteration: int = 0
    current_max: int = 0
    # Task yang di-HOLD karena diinterupsi pesan mendesak. Disimpan agar Zeline
    # ingat & bisa menawarkan melanjutkannya setelah pesan mendesak selesai —
    # seperti asisten yang tidak lupa pekerjaan yang ditunda. None = tidak ada
    # yang tertunda. Diisi saat interrupt(), dibersihkan saat di-resume/di-drop.
    held_task: str | None = None
    held_at: float = 0.0
    # Topik TERAKHIR yang dibahas user (bukan pesan pertama sesi). Diperbarui
    # tiap turn agar "lanjut" merujuk ke pekerjaan terbaru, bukan sesi awal.
    # Disimpan terpisah dari title karena title bisa stuck di pesan pertama.
    last_topic: str = ""
    last_topic_at: float = 0.0


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
                # Update last_topic SETIAP turn — ini yang dipakai "lanjut" untuk
                # merujuk ke pekerjaan terbaru, bukan title yang stuck di awal.
                # Skip pesan yang cuma greeting/command (tidak bermakna topik).
                first_line = text.strip().splitlines()[0].strip()
                if first_line and not first_line.startswith("/") and len(first_line) > 2:
                    session.last_topic = first_line[:200]
                    session.last_topic_at = time.monotonic()

            def take_steer() -> str | None:
                with self._lock:
                    return session.steer_queue.pop(0) if session.steer_queue else None

            def track_iteration(iteration: int, maximum: int) -> None:
                # Rekam progres turn untuk banner interupsi; teruskan ke callback
                # UI kalau ada.
                with self._lock:
                    session.current_iteration = iteration
                    session.current_max = maximum
                if on_iteration:
                    on_iteration(iteration, maximum)

            try:
                with self._lock:
                    session.turn_started = time.monotonic()
                    session.current_iteration = 0
                    session.current_max = 0
                reply = session.agent.send(
                    text,
                    on_tool=on_tool,
                    on_tool_result=on_tool_result,
                    on_iteration=track_iteration,
                    should_stop=session.cancel_event.is_set,
                    take_steer=take_steer,
                    on_narration=on_narration,
                    on_stream_delta=on_stream_delta,
                    turn_extra=system_extra,
                )
                session.last_used = time.monotonic()
                # Simpan history ke disk setelah tiap turn sukses → bertahan
                # lintas restart gateway. Simpan last_topic juga agar restore
                # bisa pakai konteks terbaru, bukan title awal.
                if self._persistence is not None:
                    try:
                        self._persistence.save(
                            identity,
                            session.agent.export_history(),
                            session.last_topic or session.title,
                        )
                    except Exception:
                        pass
                    # Arsip permanen: simpan user + assistant turn ini agar bisa
                    # di-recall lintas /new (bukan cuma window aktif). Best-effort.
                    try:
                        topic = session.last_topic or session.title
                        self._persistence.append_turn(identity, "user", text, topic)
                        self._persistence.append_turn(identity, "assistant", reply, topic)
                    except Exception:
                        pass
                return reply
            finally:
                with self._lock:
                    session.running = False
                    session.steer_queue.clear()
                    # Kalau turn ini membawa pengingat task tertunda (system_extra
                    # dari interupsi), lepas penandanya setelah selesai — Zeline
                    # sudah diberi kesempatan menawarkan lanjut, jadi jangan
                    # mengingatkan berulang di turn-turn berikutnya.
                    if system_extra:
                        session.held_task = None
                        session.held_at = 0.0

    def stop(self, identity: str) -> bool:
        with self._lock:
            session = self._sessions.get(identity)
            if session is None or not session.running:
                return False
            session.cancel_event.set()
            agent = getattr(session, "agent", None)
            if agent is not None and hasattr(agent, "force_cancel"):
                try:
                    agent.force_cancel()
                except Exception:
                    pass
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

    #: Penanda pesan mendesak yang harus MENGINTERUPSI task berjalan (bukan
    #: sekadar disisipkan sebagai catatan). Cocokkan sebagai kata utuh, case-
    #: insensitive. User bisa menambah lewat config nanti; untuk sekarang daftar
    #: ini menutup mayoritas "stop/ganti/prioritas/sekarang".
    # Patterns that are ALWAYS urgent regardless of context — these are
    # unambiguous stop/abort/redirect commands.
    _URGENT_HARD_PATTERNS = (
        r"\bstop\b", r"\bberhenti\b", r"\bbatal\b", r"\bcancel\b",
        r"\bhentikan\b", r"\burgent\b",
        r"\btunggu\s+dulu\b",
        r"\bkoreksi\b", r"\brevisi\b",
        r"\bprioritas\w*\b", r"\bduluan\b",
        r"\bcepet\b", r"\bcepat\b",
    )

    # Patterns that are urgent ONLY when the message is SHORT (<=8 words) —
    # short = standalone command; long = guidance/refinement embedded in a
    # sentence (e.g. "jadi sl di 14-16$ karena risk 15$ jangan di 19$ okey").
    _URGENT_SHORT_PATTERNS = (
        r"\bganti\b", r"\bubah\b",
        r"\bjangan\b",
        r"\bsalah\b", r"\bbukan\b", r"\bmalah\b",
    )

    def classify_steer(self, text: str) -> bool:
        """True if this mid-turn message is URGENT (should interrupt the task).

        Pure keyword heuristic (no API call):
        - Hard patterns → always urgent (unambiguous stop/abort words).
        - Soft patterns → urgent only when the message is ≤8 words, so that
          refinement sentences like "jadi sl di 14-16$ jangan di 19$ okey"
          are treated as steer guidance instead of an interrupt.
        - Plain questions ("btw harga eth berapa") → ordinary steer (waits).
        """
        low = f" {text.strip().lower()} "
        if any(re.search(p, low) for p in self._URGENT_HARD_PATTERNS):
            return True
        word_count = len(text.strip().split())
        if word_count <= 8:
            if any(re.search(p, low) for p in self._URGENT_SHORT_PATTERNS):
                return True
        return False

    def progress(self, identity: str) -> tuple[int, int, float] | None:
        """(iteration, max_iteration, elapsed_seconds) turn berjalan, atau None."""
        with self._lock:
            session = self._sessions.get(identity)
            if session is None or not session.running:
                return None
            elapsed = (time.monotonic() - session.turn_started) if session.turn_started else 0.0
            return (session.current_iteration, session.current_max, elapsed)

    def interrupt(self, identity: str, text: str, *, held_task: str | None = None) -> tuple[int, int, float] | None:
        """Interupsi turn berjalan agar pesan MENDESAK dikerjakan lebih dulu.

        Mengembalikan progres turn yang diinterupsi (iteration, max, elapsed)
        untuk banner "⚡ Interrupting…", atau None kalau tidak ada turn berjalan.
        ``held_task`` (teks task yang sedang dikerjakan) disimpan agar Zeline
        INGAT pekerjaan yang ditunda dan bisa menawarkan melanjutkannya nanti.
        Turn berjalan dibatalkan agar pesan mendesak jalan segera sebagai turn
        baru.
        """
        with self._lock:
            session = self._sessions.get(identity)
            if session is None or not session.running:
                return None
            elapsed = (time.monotonic() - session.turn_started) if session.turn_started else 0.0
            prog = (session.current_iteration, session.current_max, elapsed)
            # Ingat task yang ditunda (kalau ada & belum ada yang tersimpan).
            if held_task:
                session.held_task = held_task.strip()[:500]
                session.held_at = time.monotonic()
            session.cancel_event.set()
            agent = getattr(session, "agent", None)
            if agent is not None and hasattr(agent, "force_cancel"):
                try:
                    agent.force_cancel()
                except Exception:
                    pass
            return prog

    def held_task(self, identity: str) -> str | None:
        """Task yang sedang di-HOLD karena interupsi, atau None. Read-only."""
        with self._lock:
            session = self._sessions.get(identity)
            return session.held_task if session is not None else None

    def session_title(self, identity: str) -> str | None:
        """Judul sesi berjalan (≈ teks task yang sedang dikerjakan), atau None."""
        with self._lock:
            session = self._sessions.get(identity)
            if session is None:
                return None
            title = (session.title or "").strip()
            return title if title and title != "New Session" else None

    def clear_held_task(self, identity: str) -> None:
        """Lepas penanda task tertunda (setelah di-resume atau di-drop)."""
        with self._lock:
            session = self._sessions.get(identity)
            if session is not None:
                session.held_task = None
                session.held_at = 0.0

    def reset(self, identity: str) -> bool:
        with self._lock:
            session = self._sessions.pop(identity, None)
            if session is not None:
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
