"""Zeline App gateway — registrasi, sanitasi, model data, validasi config.

Setiap test yang menulis data mengarahkan ``ZELINE_APP_DATA_DIR`` ke direktori
temporer. Tanpa itu test menulis ke ``~/.zeline/app`` milik user sungguhan — itu
yang dulu membuat roster agent penuh oleh sisa fixture ("Test Agent", model
"test") yang tidak pernah dia buat.

unittest, bukan pytest: CI menjalankan ``python -m unittest discover`` dan
pytest tidak terpasang di sana.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from zeline.gateways import GATEWAYS, zeline_app
from zeline import app_auth, app_data, tool_events


class IsolatedAppData(unittest.TestCase):
    """Base class: setiap test punya direktori data sendiri."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = self._dir.name
        patcher = mock.patch.dict(os.environ, {"ZELINE_APP_DATA_DIR": self.data_dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)


class RegistryTests(unittest.TestCase):
    def test_gateway_registered(self):
        self.assertIn("zeline_app", GATEWAYS)

    def test_validate_config_accepts_a_long_token_and_valid_port(self):
        self.assertEqual(zeline_app.validate_config({"token": "t" * 20, "port": 8082}), [])

    def test_validate_config_rejects_short_token_and_bad_port(self):
        self.assertTrue(zeline_app.validate_config({"token": "short", "port": 8082}))
        self.assertTrue(zeline_app.validate_config({"token": "t" * 20, "port": 99999}))


class SanitizationTests(unittest.TestCase):
    def test_api_key_never_survives_sanitization(self):
        agent = {"id": "t1", "provider": "9Router", "api_key": "secret123", "model": "kimi"}
        clean = app_auth.sanitize_for_client(agent)
        self.assertNotIn("api_key", clean)
        self.assertEqual(clean.get("id"), "t1")


class ToolEventTests(unittest.TestCase):
    def test_event_carries_its_identity(self):
        event = tool_events.make_tool_event(
            "tool.started", "a1", "s1", tool_name="run_shell", payload={"command": "ls"})
        self.assertEqual(event["event"], "tool.started")
        self.assertEqual(event["agent_id"], "a1")


class AppDataTests(IsolatedAppData):
    def test_add_and_load_round_trip(self):
        agent = app_data.add_agent({"name": "Test Agent", "provider": "default", "model": "test"})
        self.assertTrue(agent.get("id"))
        self.assertEqual(agent.get("name"), "Test Agent")
        self.assertTrue(any(a.get("name") == "Test Agent" for a in app_data.load_agents()))

    def test_writes_stay_inside_the_override_dir(self):
        """The override is the whole reason a user's roster stays clean."""
        app_data.add_agent({"name": "Scoped", "provider": "default", "model": "test"})
        self.assertTrue(os.path.exists(os.path.join(self.data_dir, "agents.json")))


if __name__ == "__main__":
    unittest.main()
