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
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from zeline import config

# Batas defensif: jangan simpan history tak terbatas untuk bot publik.
MAX_STORED_MESSAGES = 60
MAX_STORED_CHARS = 200_000

#: Berapa kali ``limit`` kandidat yang ditarik sebelum peringkat difusikan.
#: Fusi hanya bisa mengangkat hasil yang ADA di pool, jadi pool sebesar limit
#: membuat fusi tidak berpengaruh sama sekali.
RANK_POOL_FACTOR = 5

#: Judul yang tidak menandai topik apa pun, jadi tidak bisa dipakai sebagai
#: pembatas thread di ``last_thread``.
_UNTITLED = {"", "new session", "active task"}


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
        self._ensure_archive_schema()

    def _connect(self) -> sqlite3.Connection:
        """Buka koneksi baru; pemanggil WAJIB menutupnya (lihat ``closing``).

        ``with sqlite3.connect(...) as conn`` hanya commit/rollback transaksi —
        koneksinya TETAP terbuka. Di Windows file yang masih dipegang tidak bisa
        dihapus (WinError 32), jadi setiap pemakaian dibungkus ``closing()``.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn, conn:
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
        with self._lock, closing(self._connect()) as conn, conn:
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
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO sessions (key, title, messages, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET messages=excluded.messages, "
                "title=COALESCE(excluded.title, sessions.title), updated_at=excluded.updated_at",
                (_key(identity), title, payload, time.time()),
            )

    def reset(self, identity: str) -> bool:
        with self._lock, closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM sessions WHERE key = ?", (_key(identity),))
            return cur.rowcount > 0

    def list_sessions(self) -> list[dict[str, Any]]:
        """Inventory of stored sessions for `zeline session list`.

        Identities are hashed, so the raw identity cannot be recovered here --
        that is deliberate. We report the hash prefix, title, message count and
        timestamp, which is enough to see what exists without leaking chat IDs.
        """
        try:
            with self._lock, closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT key, title, messages, updated_at FROM sessions "
                    "ORDER BY updated_at DESC"
                ).fetchall()
        except sqlite3.Error:
            return []
        out: list[dict[str, Any]] = []
        for key, title, payload, updated_at in rows:
            try:
                count = len(json.loads(payload))
            except (json.JSONDecodeError, TypeError):
                count = 0
            out.append({
                "key": str(key)[:12],
                "title": title or "",
                "messages": count,
                "updated_at": float(updated_at or 0.0),
            })
        return out

    # ------------------------------------------------------------------
    # Archive: transkrip percakapan permanen (tidak dihapus /new atau trim).
    #
    # ``sessions`` di atas hanya menyimpan window aktif (60 pesan, dihapus saat
    # /new). Itu bikin bot "amnesia": user bilang "lanjut file tadi" di sesi baru
    # → tidak ada konteksnya. Archive menyimpan SETIAP user/assistant turn secara
    # permanen dengan FTS5 full-text search, sehingga tool recall_history bisa
    # menarik apa yang benar-benar dibahas di masa lalu — bukan menebak file.
    # ------------------------------------------------------------------
    def _ensure_archive_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS archive ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  key TEXT NOT NULL,"
                "  role TEXT NOT NULL,"
                "  content TEXT NOT NULL,"
                "  title TEXT,"
                "  ts REAL NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_key ON archive(key, ts)"
            )
            # FTS5 virtual table untuk pencarian isi percakapan.
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5("
                "  content, key UNINDEXED, role UNINDEXED, ts UNINDEXED,"
                "  content='archive', content_rowid='id'"
                ")"
            )
            # Trigger sinkronisasi archive → archive_fts.
            conn.execute(
                "CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON archive BEGIN "
                "  INSERT INTO archive_fts(rowid, content) VALUES (new.id, new.content); "
                "END"
            )

    def append_turn(self, identity: str, role: str, content: str, title: str | None = None) -> None:
        """Tambah satu pesan (user/assistant) ke archive permanen.

        Best-effort: kegagalan di sini tidak boleh menghentikan percakapan.
        """
        text = (content or "").strip()
        if not text or role not in {"user", "assistant"}:
            return
        try:
            with self._lock, closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO archive (key, role, content, title, ts) VALUES (?, ?, ?, ?, ?)",
                    (_key(identity), role, text[:MAX_STORED_CHARS], title, time.time()),
                )
        except Exception:
            pass

    def search_archive(self, identity: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Cari transkrip percakapan lama (FTS5) untuk identity ini.

        Peringkat = FUSI relevansi + kebaruan, bukan bm25 murni. Alasannya
        terukur: dengan ``ORDER BY bm25(), ts DESC``, kebaruan cuma tie-break dan
        praktis tidak pernah dipakai karena skor bm25 hampir selalu berbeda.
        Topik LAMA yang panjang dan sering menyebut kata umum ("lanjut") menang
        atas topik TERBARU — probe di device ini: query 'lanjut' mengembalikan
        8/8 turn topik lama dan 0 turn topik terbaru. Itu persis keluhan
        "disuruh lanjut malah balik ke sesi pertama".

        Fusi peringkat (Borda): ambil kandidat lebih banyak dari ``limit``, beri
        peringkat menurut bm25 dan menurut ``ts`` menurun, lalu urutkan menurut
        jumlah kedua peringkat (seri → yang terbaru menang). Efeknya: kecocokan
        kata kunci yang benar-benar kuat masih bisa menang dari masa lalu, tapi
        kecocokan yang setara selalu dimenangkan percakapan terakhir.
        """
        q = (query or "").strip()
        if not q:
            return []
        # Sanitasi query FTS5: buang karakter yang bikin syntax error, jadikan
        # OR antar-kata biar recall lebih longgar (mirip session_search).
        terms = [t for t in re.findall(r"[\w]+", q, flags=re.UNICODE) if len(t) > 1]
        if not terms:
            return []
        match_expr = " OR ".join(terms)
        want = max(1, int(limit))
        pool = max(want * RANK_POOL_FACTOR, want)
        try:
            with self._lock, closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT a.role, a.content, a.ts, a.title, bm25(archive_fts) AS score "
                    "FROM archive_fts f JOIN archive a ON a.id = f.rowid "
                    "WHERE f.key = ? AND archive_fts MATCH ? "
                    "ORDER BY score LIMIT ?",
                    (_key(identity), match_expr, int(pool)),
                ).fetchall()
        except Exception:
            return []
        if not rows:
            return []
        # rows sudah terurut menurut relevansi → posisinya ADALAH peringkat
        # relevansi. Kita pakai indeks posisi (bukan identitas objek baris:
        # dua baris dengan nilai identik bisa saling menimpa di dict).
        relevance_rank = list(range(len(rows)))
        recency_order = sorted(relevance_rank, key=lambda i: float(rows[i][2] or 0.0), reverse=True)
        recency_rank = [0] * len(rows)
        for rank, index in enumerate(recency_order):
            recency_rank[index] = rank
        fused = sorted(
            relevance_rank,
            key=lambda i: (relevance_rank[i] + recency_rank[i], -float(rows[i][2] or 0.0)),
        )[:want]
        return [self._archive_row(rows[i][0], rows[i][1], rows[i][2], rows[i][3]) for i in fused]

    @staticmethod
    def _archive_row(role: Any, content: Any, ts: Any, title: Any) -> dict[str, Any]:
        return {
            "role": role,
            "content": content,
            "when": datetime.fromtimestamp(float(ts or 0.0)).strftime("%Y-%m-%d %H:%M"),
            "title": title or "",
        }

    def last_thread(self, identity: str, limit: int = 12) -> list[dict[str, Any]]:
        """Turn terakhir yang masih SATU topik dengan pesan paling baru.

        Ini anchor deterministik untuk "lanjut / lanjutin / terusin". Pencarian
        kata kunci tidak bisa menjawab itu — "lanjut" bukan topik, ia rujukan ke
        *yang terakhir dikerjakan*. Kita ambil ``title`` dari turn terbaru lalu
        mundur selama title-nya masih sama, jadi hasilnya satu percakapan utuh
        dan tidak tercampur topik sebelumnya.

        Judul kosong/``New Session`` tidak bisa dipakai sebagai pembatas topik,
        jadi untuk kasus itu kita jatuh ke N turn terakhir secara kronologis.
        """
        want = max(1, int(limit))
        try:
            with self._lock, closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT role, content, ts, title FROM archive WHERE key = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (_key(identity), want * 4),
                ).fetchall()
        except Exception:
            return []
        if not rows:
            return []
        head_title = (rows[0][3] or "").strip()
        if head_title and head_title.lower() not in _UNTITLED:
            same: list[Any] = []
            for row in rows:
                if (row[3] or "").strip() != head_title:
                    break
                same.append(row)
            rows = same[:want]
        else:
            rows = rows[:want]
        out = [self._archive_row(r[0], r[1], r[2], r[3]) for r in rows]
        out.reverse()  # kronologis
        return out

    def recent_archive(self, identity: str, limit: int = 10) -> list[dict[str, Any]]:
        """Ambil N turn terakhir dari archive (buat 'apa yang tadi dibahas')."""
        try:
            with self._lock, closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT role, content, ts, title FROM archive WHERE key = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (_key(identity), int(limit)),
                ).fetchall()
        except Exception:
            return []
        out = [
            {
                "role": r[0],
                "content": r[1],
                "when": datetime.fromtimestamp(r[2]).strftime("%Y-%m-%d %H:%M"),
                "title": r[3] or "",
            }
            for r in rows
        ]
        out.reverse()  # kronologis
        return out

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
