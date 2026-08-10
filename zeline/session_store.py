"""Persistensi history percakapan Zeline ke SQLite.

Tanpa ini, history hanya hidup di RAM ``SessionStore`` dan HILANG setiap gateway
restart — bot jadi "tiba-tiba lupa" percakapan sebelumnya. Modul ini menyimpan
message history tiap identity ke ``~/.zeline/sessions.db`` sehingga:

- restart gateway tidak menghapus konteks percakapan,
- tiap identity tetap terisolasi (telegram:123 != telegram:456),
- bot publik tetap aman (ukuran per-identity dibatasi, identity di-hash).

Desain minimalis & tanpa dependency eksternal (sqlite3 bawaan Python):
- 1 baris per identity menyimpan JSON message list + metadata (title, updated_at).
- Identity di-hash SHA-256 seperti memory, jadi nomor/chat ID tidak bocor.
- History disimpan penuh setelah tiap turn; saat dimuat dipangkas ke batas aman.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from zeline import config

# Batas defensif: jangan simpan history tak terbatas untuk bot publik.
MAX_STORED_MESSAGES = 60
MAX_STORED_CHARS = 200_000


def _db_path() -> Path:
    return config.DATA_DIR / "sessions.db"


def _key(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


class SessionPersistence:
    """Penyimpan history percakapan berbasis SQLite, aman dipakai multi-thread."""

    def __init__(self, path: Path | None = None):
        self.path = path or _db_path()
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "  key TEXT PRIMARY KEY,"
                "  title TEXT,"
                "  messages TEXT NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def load(self, identity: str) -> tuple[list[dict[str, Any]], str | None]:
        """Kembalikan (messages, title) untuk sebuah identity, atau ([], None)."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT messages, title FROM sessions WHERE key = ?",
                (_key(identity),),
            ).fetchone()
        if not row:
            return [], None
        try:
            messages = json.loads(row[0])
            if not isinstance(messages, list):
                return [], None
        except (json.JSONDecodeError, TypeError):
            return [], None
        return messages, (row[1] if row[1] else None)

    def save(self, identity: str, messages: list[dict[str, Any]], title: str | None = None) -> None:
        """Simpan history penuh (dipangkas ke batas aman) untuk sebuah identity."""
        trimmed = self._trim(messages)
        payload = json.dumps(trimmed, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (key, title, messages, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET messages=excluded.messages, "
                "title=COALESCE(excluded.title, sessions.title), updated_at=excluded.updated_at",
                (_key(identity), title, payload, time.time()),
            )

    def reset(self, identity: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE key = ?", (_key(identity),))
            return cur.rowcount > 0

    def _trim(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Batasi jumlah & ukuran, tapi jaga integritas tool-call protocol.

        Selalu pertahankan system message (index 0 bila ada) dan potong hanya di
        awal user turn agar tidak meninggalkan assistant(tool_calls) tanpa hasil
        tool-nya (yang akan ditolak provider).
        """
        if not messages:
            return []
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        body = messages[1:] if system else messages
        if len(body) > MAX_STORED_MESSAGES:
            tail = body[-MAX_STORED_MESSAGES:]
            start = next((i for i, m in enumerate(tail) if m.get("role") == "user"), 0)
            body = tail[start:]
        # Batasi karakter total dari belakang (percakapan terbaru diutamakan).
        total = 0
        kept: list[dict[str, Any]] = []
        for message in reversed(body):
            size = len(str(message.get("content", "")))
            if total + size > MAX_STORED_CHARS and kept:
                break
            total += size
            kept.append(message)
        kept.reverse()
        # Jangan biarkan window dimulai dari assistant/tool orphan.
        start = next((i for i, m in enumerate(kept) if m.get("role") == "user"), 0)
        kept = kept[start:]
        return ([system] + kept) if system else kept
