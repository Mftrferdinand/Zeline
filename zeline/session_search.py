"""FTS5 full-text search across all Zeline sessions.

Indexes conversation history (markdown) and episodes (JSON) into a SQLite
FTS5 virtual table. The agent can search past sessions with
``search_sessions(query)`` — like leading agents' FTS5 session search.

Index is rebuilt incrementally: new files are indexed on search if their
mtime is newer than the last index run.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def _db_path() -> Path:
    return Path.home() / ".zeline" / "sessions_fts.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
            source,      -- 'conversation' or 'episode'
            identifier,  -- filename or episode id
            content,
            tokenize='porter unicode61'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indexed_files (
            path TEXT PRIMARY KEY,
            mtime REAL
        )
    """)
    conn.commit()


def _index_conversations(conn: sqlite3.Connection) -> int:
    """Index conversation history markdown files. Returns files indexed."""
    hist_dir = Path.home() / ".zeline" / "conversation_history"
    if not hist_dir.is_dir():
        return 0
    count = 0
    for md in sorted(hist_dir.glob("*.md")):
        mtime = md.stat().st_mtime
        row = conn.execute(
            "SELECT mtime FROM indexed_files WHERE path = ?", (str(md),)).fetchone()
        if row and row[0] >= mtime:
            continue  # already indexed
        try:
            content = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Remove existing entries for this file, then insert fresh
        conn.execute("DELETE FROM sessions_fts WHERE identifier = ?", (md.name,))
        conn.execute(
            "INSERT INTO sessions_fts (source, identifier, content) VALUES (?, ?, ?)",
            ("conversation", md.name, content),
        )
        conn.execute(
            "INSERT OR REPLACE INTO indexed_files (path, mtime) VALUES (?, ?)",
            (str(md), mtime),
        )
        count += 1
    conn.commit()
    return count


def _index_episodes(conn: sqlite3.Connection) -> int:
    """Index episode JSON files. Returns files indexed."""
    ep_dir = Path.home() / ".zeline" / "episodes"
    if not ep_dir.is_dir():
        return 0
    count = 0
    for jf in sorted(ep_dir.glob("*.json")):
        mtime = jf.stat().st_mtime
        row = conn.execute(
            "SELECT mtime FROM indexed_files WHERE path = ?", (str(jf),)).fetchone()
        if row and row[0] >= mtime:
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        # Flatten episode events into searchable text
        parts: list[str] = []
        if isinstance(data, dict):
            for key in ("title", "summary", "description"):
                if data.get(key):
                    parts.append(str(data[key]))
            events = data.get("events", [])
            if isinstance(events, list):
                for ev in events:
                    if isinstance(ev, dict):
                        parts.append(str(ev.get("text", ev.get("description", ""))))
                    elif isinstance(ev, str):
                        parts.append(ev)
        content = "\n".join(p for p in parts if p)
        if not content:
            continue
        ep_id = data.get("id", jf.stem) if isinstance(data, dict) else jf.stem
        conn.execute("DELETE FROM sessions_fts WHERE identifier = ?", (jf.name,))
        conn.execute(
            "INSERT INTO sessions_fts (source, identifier, content) VALUES (?, ?, ?)",
            ("episode", str(ep_id), content),
        )
        conn.execute(
            "INSERT OR REPLACE INTO indexed_files (path, mtime) VALUES (?, ?)",
            (str(jf), mtime),
        )
        count += 1
    conn.commit()
    return count


def ensure_indexed() -> int:
    """Index any new/changed files. Returns total files indexed this run."""
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        _ensure_schema(conn)
        n = _index_conversations(conn) + _index_episodes(conn)
        return n
    finally:
        conn.close()


def search_sessions(query: str, limit: int = 5) -> list[dict]:
    """Full-text search across all sessions.

    Returns up to ``limit`` matches as dicts with keys:
    source, identifier, snippet (with <b> highlights).
    Automatically indexes new files before searching.
    """
    ensure_indexed()
    conn = sqlite3.connect(str(_db_path()))
    try:
        # Sanitize query for FTS5: strip quotes and escape embedded quotes (M4 fix)
        terms = []
        for t in query.split():
            t = t.replace('"', '""').strip('"')
            if t:
                terms.append(t)
        if not terms:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in terms)
        rows = conn.execute(
            """
            SELECT source, identifier,
                   snippet(sessions_fts, 2, '<b>', '</b>', '...', 30)
            FROM sessions_fts
            WHERE sessions_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return [
            {"source": r[0], "identifier": r[1], "snippet": r[2]}
            for r in rows
        ]
    finally:
        conn.close()


def index_stats() -> dict:
    """Return index statistics."""
    ensure_indexed()
    conn = sqlite3.connect(str(_db_path()))
    try:
        total = conn.execute("SELECT COUNT(*) FROM sessions_fts").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0]
        return {"documents": total, "files_indexed": files}
    finally:
        conn.close()
