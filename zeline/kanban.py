"""Simple kanban board for Zeline.

A single board with three columns: todo, doing, done.
Cards are stored as JSON in ~/.zeline/kanban.json (atomic write, 0600).

Kept intentionally simple: no swimlanes, no labels, no WIP limits.

Error contract: validation problems raise ``ValueError``. Disk/IO failures
(e.g. in ``_save``) raise ``OSError`` — callers that talk to the model should
catch it and render a friendly message (``tools.py`` wrappers do this).
"""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = ("todo", "doing", "done")

_KANBAN_FILE = Path.home() / ".zeline" / "kanban.json"
_lock = threading.Lock()

# Cache board terakhir berdasarkan mtime file — hemat re-read + re-parse
# untuk operasi beruntun. Semua akses lewat _lock (lihat fungsi public),
# jadi tidak perlu lock tambahan di sini.
_cache: dict = {"mtime_ns": None, "board": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_board() -> dict:
    return {"cards": []}


def _load() -> dict:
    # mtime cache: kalau file tidak berubah sejak load terakhir, pakai
    # salinan board yang sudah di-parse (deepcopy agar caller tidak
    # memutasi cache secara tidak sengaja).
    global _cache
    try:
        st = _KANBAN_FILE.stat()
    except OSError:
        st = None
    if (
        st is not None
        and _cache["mtime_ns"] == st.st_mtime_ns
        and _cache["board"] is not None
    ):
        return copy.deepcopy(_cache["board"])
    # Cap 10MB — file raksasa = corrupt/aneh, jangan OOM. Di luar try agar
    # ValueError-nya ikut masuk jalur backup+empty-board.
    try:
        if st is None:
            raise OSError("kanban file missing")
        if st.st_size > 10 * 1024 * 1024:
            raise ValueError("kanban file too large (>10MB)")
        data = json.loads(_KANBAN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _backup_corrupt(exc)
        data = _empty_board()
        st = None
    else:
        if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
            _backup_corrupt(ValueError("invalid board shape"))
            data = _empty_board()
            st = None
    _cache = {
        "mtime_ns": st.st_mtime_ns if st is not None else None,
        "board": data,
    }
    # Selalu deepcopy — cache-miss path juga tidak boleh return objek yang
    # sama dengan cache (phantom card kalau caller mutasi sebelum _save).
    return copy.deepcopy(data)


def _backup_corrupt(exc: BaseException) -> None:
    """Simpan file corrupt ke .bak sebelum reset — jangan fail-open diam-diam."""
    try:
        if _KANBAN_FILE.is_file():
            bak = _KANBAN_FILE.with_suffix(".json.bak")
            _KANBAN_FILE.replace(bak)
    except OSError:
        pass


def _save(board: dict) -> None:
    """Tulis board secara atomic (tmp + 0600 + rename).

    Raises ``OSError`` bila disk gagal (penuh, permission) — file lama
    tetap utuh berkat rename atomic. Lihat kontrak error di docstring modul.
    """
    global _cache
    _KANBAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _KANBAN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(board, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, _KANBAN_FILE)
    try:
        os.chmod(_KANBAN_FILE, 0o600)
    except OSError:
        pass
    # Segarkan cache: operasi berikutnya tidak perlu re-read.
    try:
        mtime_ns = _KANBAN_FILE.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    _cache = {"mtime_ns": mtime_ns, "board": board}


def _validate_column(column: str) -> str:
    col = str(column or "").strip().lower()
    if col not in COLUMNS:
        raise ValueError(f"column must be one of {list(COLUMNS)}, got {column!r}")
    return col


def create_card(title: str, description: str = "", column: str = "todo") -> str:
    """Create a card. Returns the card id."""
    title = str(title or "").strip()
    if not title:
        raise ValueError("title is required")
    col = _validate_column(column)
    card_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    card = {
        "id": card_id,
        "title": title[:200],
        "description": str(description or "")[:2000],
        "column": col,
        "created": now,
        "updated": now,
    }
    with _lock:
        board = _load()
        board["cards"].append(card)
        _save(board)
    return card_id


def get_card(card_id: str) -> dict | None:
    """Get a single card by id, or None."""
    cid = str(card_id or "").strip()
    if not cid:
        return None
    with _lock:
        board = _load()
        for card in board["cards"]:
            if card.get("id") == cid:
                return dict(card)
    return None


def move_card(card_id: str, to_column: str) -> dict:
    """Move a card to another column. Returns the updated card."""
    col = _validate_column(to_column)
    cid = str(card_id or "").strip()
    if not cid:
        raise ValueError("card_id is required")
    with _lock:
        board = _load()
        for card in board["cards"]:
            if card.get("id") == cid:
                card["column"] = col
                card["updated"] = _now_iso()
                _save(board)
                return dict(card)
    raise ValueError(f"card not found: {cid!r}")


def list_cards(column: str | None = None) -> list[dict]:
    """List cards, optionally filtered by column."""
    col = None
    if column is not None:
        col = _validate_column(column)
    with _lock:
        board = _load()
        cards = [dict(c) for c in board["cards"]]
    if col is not None:
        cards = [c for c in cards if c.get("column") == col]
    return cards


def delete_card(card_id: str) -> bool:
    """Delete a card. Returns True if it existed."""
    cid = str(card_id or "").strip()
    if not cid:
        return False
    with _lock:
        board = _load()
        before = len(board["cards"])
        board["cards"] = [c for c in board["cards"] if c.get("id") != cid]
        if len(board["cards"]) == before:
            return False
        _save(board)
    return True


def board_summary() -> dict:
    """Count cards per column."""
    cards = list_cards()
    summary = {c: 0 for c in COLUMNS}
    for card in cards:
        col = card.get("column")
        if col in summary:
            summary[col] += 1
    return {"total": len(cards), "columns": summary}
