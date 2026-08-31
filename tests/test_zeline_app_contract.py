"""Kontrak Zeline App — schema respons, auth, sanitasi, event, streaming.

Semua test yang menulis memakai ``isolated_app_data`` (ZELINE_APP_DATA_DIR →
tmp_path). Tanpa itu fixture mengotori ``~/.zeline/app`` milik user.
"""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

import pytest

from zeline.gateways import GATEWAYS, zeline_app
from zeline.gateways import zeline_app_runtime as runtime
from zeline import app_auth, app_data, tool_events

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENT_MOBILE_FIELDS = (
    "id", "name", "avatar", "description", "provider_id", "model",
    "system_instructions", "enabled_tools", "enabled_skills", "memory_enabled",
    "created_at",
)
SECRET_FIELDS = ("api_key", "secret", "token", "credential")


@pytest.fixture
def isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ZELINE_APP_DATA_DIR", str(tmp_path))
    return tmp_path


def test_contract_endpoints_registered():
    assert zeline_app.info().get("label") == "Zeline App"
    assert "zeline_app" in GATEWAYS


def test_auth_token_roundtrip_and_rejects_foreign_secret():
    secret = secrets.token_bytes(32)
    token = app_auth.generate_token("agent_test", secret=secret)
    payload = app_auth.verify_token(token, secret=secret)
    assert payload is not None
    assert "agent_ref" in payload and "scope" in payload
    assert app_auth.verify_token(token, secret=secrets.token_bytes(32)) is None


def test_sanitize_removes_secrets():
    agent = {"id": "a1", "name": "Agent", "provider": "9router",
             "api_key": "secret123", "token": "t", "credential": "x"}
    clean = app_auth.sanitize_for_client(agent)
    for field in ("api_key", "token", "credential"):
        assert field not in clean
    assert clean.get("id") == "a1"


def test_agent_model_has_every_mobile_field_and_no_secret(isolated_app_data):
    """The app renders these fields directly; a missing one is a blank row."""
    agent = app_data.add_agent({
        "name": "Coding",
        "avatar": "icon-coding",
        "description": "Coding agent",
        "provider_id": "p1",
        "model": "kimi",
        "system_instructions": "Be concise.",
        "enabled_tools": ["run_shell"],
        "enabled_skills": ["marketanalysis"],
        "memory_enabled": True,
    })
    for field in AGENT_MOBILE_FIELDS:
        assert field in agent, f"field {field} hilang dari agent profile"
    clean = app_auth.sanitize_for_client(agent)
    for bad in SECRET_FIELDS:
        assert bad not in clean, f"secret {bad} bocor ke client"


def test_master_gateway_roster_marks_only_configured_agent_tokens_connected(isolated_app_data):
    """Connected means this gateway explicitly stores the profile's Agent Token."""
    agent = app_data.add_agent({"name": "Live", "provider_id": "p1", "model": "m1"})
    app_data.add_session({"id": "sess_live", "session_id": "sess_live", "agent_id": agent["id"]})
    assert agent["id"] in zeline_app._connected_agent_ids(None, [agent["agent_api_token"]])
    assert zeline_app._connected_agent_ids(None, []) == set()


def test_session_isolation(isolated_app_data):
    first = app_data.add_session({"agent_id": "a1", "title": "Chat 1"})
    second = app_data.add_session({"agent_id": "a2", "title": "Chat 2"})
    assert first["session_id"] != second["session_id"]


def test_provider_refs_never_expose_a_full_key():
    for ref in app_data.list_provider_refs():
        assert "api_key" not in ref
        hint = ref.get("api_key_hint")
        if hint is not None:
            assert "••" in str(hint) or len(str(hint)) <= 4


def test_tool_event_schema():
    event = tool_events.make_tool_event(
        "tool.started", "a", "s", tool_name="run_shell", payload={"command": "ls"})
    for key in ("event", "agent_id", "session_id", "tool_name", "status", "payload", "gateway"):
        assert key in event, f"field {key} hilang dari event"


def test_stream_steer_queue_only_accepts_active_sessions():
    sid = "sess_steer_contract"
    assert runtime.stream_steer(sid, "focus here") is False
    runtime.stream_start(sid, "stream_contract")
    assert runtime.stream_steer(sid, "focus here") is True
    assert runtime.take_stream_steer(sid) == "focus here"
    assert runtime.take_stream_steer(sid) is None
    runtime.stream_finish(sid)


def test_cancel_is_idempotent_and_scoped_to_an_active_stream():
    """The app taps stop twice; the second tap must not claim a fresh cancel."""
    sid = "sess_cancel_contract"
    assert runtime.stream_cancel(sid) == (False, None)
    runtime.stream_start(sid, "stream_cancel_contract")
    cancelled, stream_id = runtime.stream_cancel(sid)
    assert cancelled is True and stream_id == "stream_cancel_contract"
    assert runtime.is_cancelled(sid) is True
    runtime.stream_finish(sid)
    assert runtime.stream_cancel(sid) == (False, None)
    assert runtime.is_cancelled(sid) is False


def test_gateway_agent_runtime_forces_streaming(isolated_app_data, monkeypatch):
    """SSE promises assistant.delta and mid-stream cancel; both need streaming.

    ``agent.stream`` is a CLI preference. When it was off, this gateway emitted
    one delta at the very end and cancel could not land until the blocking
    provider request returned (up to 180s), because nothing was reading in a
    loop that could check the flag. The gateway therefore overrides it per
    instance rather than reading the global.
    """
    from zeline import config

    monkeypatch.setattr(config, "STREAM_RESPONSES", False, raising=False)
    agent = {"id": "agent_stream", "model": "m1", "provider_id": "", "system_instructions": ""}
    sid = "sess_stream_contract"
    try:
        instance = runtime.get_agent_runtime(sid, agent, "safe")
        assert instance.stream_responses is True
        assert instance._streaming_enabled() is True
    finally:
        runtime.drop_session_runtime(sid)


def test_saved_message_ids_are_deduplicated_against_real_history(isolated_app_data):
    sid = "sess_saved_contract"
    runtime.append_message(sid, runtime.new_message(sid, "agent_save", "assistant", "Important"))
    valid = runtime.load_messages(sid)[0]["id"]
    requested = [valid, valid, "missing"]
    filtered = list(dict.fromkeys(value for value in requested if value in {valid}))
    assert filtered == [valid]


def test_system_info_shape_no_ip():
    info = zeline_app._system_info()
    for key in ("kind", "os", "arch", "python", "zeline_version", "runtime", "online"):
        assert key in info, f"missing {key} in /system"
    assert "ip" not in info
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", json.dumps(info).lower())


def test_client_example_exists():
    path = REPO_ROOT / "examples" / "zeline_app_client.py"
    assert path.exists(), "client example tidak ada"
    content = path.read_text(encoding="utf-8")
    assert "auth" in content or "agent" in content
