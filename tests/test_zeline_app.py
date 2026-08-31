"""Zeline App gateway — registrasi, sanitasi, model data, validasi config.

Setiap test yang menulis data memakai ``ZELINE_APP_DATA_DIR`` lewat fixture
``isolated_app_data``. Tanpa itu test menulis ke ``~/.zeline/app`` milik user
sungguhan — itu yang dulu membuat roster agent user penuh oleh sisa fixture
("Test Agent", model "test") yang tidak pernah dia buat.
"""
from __future__ import annotations

import pytest

from zeline.gateways import GATEWAYS, zeline_app
from zeline import app_auth, app_data, tool_events


@pytest.fixture
def isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ZELINE_APP_DATA_DIR", str(tmp_path))
    return tmp_path


def test_gateway_registered():
    assert "zeline_app" in GATEWAYS


def test_app_auth_sanitize():
    agent = {"id": "t1", "provider": "9Router", "api_key": "secret123", "model": "kimi"}
    clean = app_auth.sanitize_for_client(agent)
    assert "api_key" not in clean
    assert clean.get("id") == "t1"


def test_tool_events():
    event = tool_events.make_tool_event(
        "tool.started", "a1", "s1", tool_name="run_shell", payload={"command": "ls"})
    assert event["event"] == "tool.started"
    assert event["agent_id"] == "a1"


def test_app_data(isolated_app_data):
    agent = app_data.add_agent({"name": "Test Agent", "provider": "default", "model": "test"})
    assert agent.get("id")
    assert agent.get("name") == "Test Agent"
    assert any(a.get("name") == "Test Agent" for a in app_data.load_agents())


def test_app_data_writes_only_inside_the_override_dir(isolated_app_data):
    """The override is the whole reason the user's roster stays clean."""
    app_data.add_agent({"name": "Scoped", "provider": "default", "model": "test"})
    assert (isolated_app_data / "agents.json").exists()


def test_gateway_validate():
    assert not zeline_app.validate_config({"token": "t" * 20, "port": 8082})


def test_gateway_validate_rejects_short_token_and_bad_port():
    assert zeline_app.validate_config({"token": "short", "port": 8082})
    assert zeline_app.validate_config({"token": "t" * 20, "port": 99999})
