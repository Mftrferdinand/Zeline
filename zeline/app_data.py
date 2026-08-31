"""Data model Zeline App — agent profiles, sessions, providers.

Tidak menyimpan provider API keys dalam model ini; key tetap di
zeline/config (lokal, gitignored). Model hanya menyimpan referensi
provider + konfigurasi agent (model, skill, tool profile).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _app_dir() -> Path:
    """Direktori data app.

    ``ZELINE_APP_DATA_DIR`` memungkinkan test/dev memakai direktori terisolasi
    sehingga fixture tidak mengotori agent & session milik user.
    """
    override = os.environ.get("ZELINE_APP_DATA_DIR", "").strip()
    return Path(override) if override else Path.home() / ".zeline" / "app"


# Dievaluasi per-panggilan agar override environment tetap dihormati.
def _agents_file() -> Path:
    return _app_dir() / "agents.json"


def _sessions_file() -> Path:
    return _app_dir() / "sessions.json"


# Kompatibilitas nama lama (dipakai modul/tes lain).
APP_DATA_DIR = _app_dir()
AGENTS_FILE = APP_DATA_DIR / "agents.json"
SESSIONS_FILE = APP_DATA_DIR / "sessions.json"


def _ensure_dir() -> None:
    _app_dir().mkdir(parents=True, exist_ok=True)


def _now() -> str:
    """UTC ISO-8601 dengan sufiks Z — format yang sama dipakai gateway HTTP."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_agents() -> list[dict[str, Any]]:
    _ensure_dir()
    path = _agents_file()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        changed = False
        from zeline.app_auth import generate_agent_api_token
        for existing in data:
            if not existing.get("agent_api_token"):
                existing["agent_api_token"] = generate_agent_api_token()
                changed = True
        if changed:
            save_agents(data)
        return data
    except (json.JSONDecodeError, OSError):
        return []


def save_agents(agents: list[dict[str, Any]]) -> None:
    _ensure_dir()
    with open(_agents_file(), "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def add_agent(agent: dict[str, Any]) -> dict[str, Any]:
    agents = load_agents()
    agent_id = agent.get("id") or agent.get("agent_id")
    if not agent_id:
        import secrets, time
        agent_id = f"agent_{secrets.token_urlsafe(8)}_{int(time.time())}"
    agent["id"] = agent_id
    if not agent.get("agent_api_token"):
        from zeline.app_auth import generate_agent_api_token
        agent["agent_api_token"] = generate_agent_api_token()
    # Stempel waktu di layer penyimpanan, bukan hanya di handler HTTP: agent
    # juga dibuat dari CLI, migrasi, dan test. Yang lewat jalur itu dulu tersimpan
    # tanpa created_at, dan app mengurutkan serta menampilkan field tersebut —
    # jadi hasilnya baris kosong dan urutan roster acak. setdefault dipakai agar
    # nilai yang sudah ada (mis. saat import/restore) tidak tertimpa.
    now = _now()
    agent.setdefault("created_at", now)
    agent.setdefault("updated_at", agent["created_at"])
    # Hindari duplikat ID
    agents = [a for a in agents if a.get("id") != agent_id]
    agents.append(agent)
    save_agents(agents)
    return agent


def delete_agent(agent_id: str) -> bool:
    agents = load_agents()
    before = len(agents)
    agents = [a for a in agents if a.get("id") != agent_id]
    save_agents(agents)
    return len(agents) < before


# Session metadata (bukan message history penuh — history tetap milik agent runtime)
def load_sessions() -> list[dict[str, Any]]:
    _ensure_dir()
    path = _sessions_file()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_sessions(sessions: list[dict[str, Any]]) -> None:
    _ensure_dir()
    with open(_sessions_file(), "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def add_session(session: dict[str, Any]) -> dict[str, Any]:
    sessions = load_sessions()
    sid = session.get("session_id") or session.get("id")
    if not sid:
        import secrets, time
        sid = f"sess_{secrets.token_urlsafe(8)}_{int(time.time())}"
    session["session_id"] = sid
    sessions = [s for s in sessions if s.get("session_id") != sid]
    sessions.append(session)
    save_sessions(sessions)
    return session


# Provider reference — hanya menyimpan label + endpoint, bukan key
# Key tetap di zeline/config (provider-agnostic)
def list_provider_refs() -> list[dict[str, Any]]:
    # Reuse konfigurasi zeline yang sudah ada
    from zeline import config
    refs = []
    # Ambil dari config jika tersedia
    if hasattr(config, "PROVIDERS"):
        for p in config.PROVIDERS:
            refs.append({
                "provider_ref": p.get("name", "default"),
                "endpoint": p.get("base_url"),
                "protocol": p.get("protocol", "openai"),
                "model_default": p.get("model", ""),
            })
    else:
        # Default fallback: provider aktif dari config
        refs.append({
            "provider_ref": "default",
            "endpoint": getattr(config, "BASE_URL", ""),
            "protocol": getattr(config, "PROTOCOL", "openai"),
            "model_default": getattr(config, "MODEL", ""),
        })
    return refs
