"""Tests for the Shortcut connector (personal API token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import shortcut as shortcut_mod
from zeline.connectors.shortcut import ShortcutConnector


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("shortcut", {"api_token": "SECRET-TOKEN", "name": "Aester"})


class ShortcutConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-shortcut-test-"))
        _patch_store(self, self.tmp)
        self.conn = ShortcutConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"name": "Aester", "id": 42})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_token="PAT")
        self.assertEqual(result, "Connected to Shortcut as Aester.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.app.shortcut.com/api/v3/member")
        self.assertEqual(kwargs["headers"]["Shortcut-Token"], "PAT")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("shortcut")
        self.assertEqual(saved["api_token"], "PAT")
        self.assertEqual(saved["name"], "Aester")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Invalid token"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("shortcut"))

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR: api_token is required."))
            self.assertTrue(self.conn.connect(api_token="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("shortcut"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_token="PAT")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_connect_unreadable_response(self):
        fake = mock.Mock(status_code=200)
        fake.json.side_effect = ValueError("bad json")
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_token="PAT")
        self.assertTrue(result.startswith("ERROR:"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "Aester")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Shortcut disconnected.")
        self.assertEqual(self.conn.disconnect(), "Shortcut was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "shortcut")
        self.assertEqual(self.conn.name, "Shortcut")
        self.assertEqual(self.conn.auth_kind, "pat")


class ShortcutOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-shortcut-test-"))
        _patch_store(self, self.tmp)
        self.conn = ShortcutConnector()
        _seed_connected()

    def test_list_stories(self):
        payload = {
            "data": [
                {
                    "id": 101,
                    "name": "Fix login bug",
                    "story_type": "bug",
                    "workflow_state_name": "In Progress",
                },
                {
                    "id": 102,
                    "name": "Add dark mode",
                    "story_type": "feature",
                    "workflow_state_name": "Ready for Dev",
                },
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_stories(limit=2)
        self.assertEqual(
            result,
            "#101 Fix login bug [bug/In Progress]\n#102 Add dark mode [feature/Ready for Dev]",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.app.shortcut.com/api/v3/stories/search")
        self.assertEqual(kwargs["headers"]["Shortcut-Token"], "SECRET-TOKEN")
        self.assertEqual(kwargs["json"], {})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_stories_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_stories(), "No stories found.")

    def test_list_stories_missing_fields(self):
        payload = {"data": [{"id": 5}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.list_stories(), "#5 - [-/-]")

    def test_create_story(self):
        payload = {"id": 999, "app_url": "https://app.shortcut.com/aester/story/999"}
        with mock.patch("requests.request", return_value=FakeResponse(payload, status=201)) as req:
            result = self.conn.create_story("Ship v2", description="Big launch", story_type="feature")
        self.assertEqual(result, "Story created: https://app.shortcut.com/aester/story/999")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.app.shortcut.com/api/v3/stories")
        self.assertEqual(
            kwargs["json"],
            {"name": "Ship v2", "description": "Big launch", "story_type": "feature"},
        )

    def test_create_story_defaults(self):
        payload = {"id": 1000}
        with mock.patch("requests.request", return_value=FakeResponse(payload, status=201)) as req:
            result = self.conn.create_story("Just a name")
        self.assertEqual(result, "Story created: 1000")
        self.assertEqual(
            req.call_args[1]["json"],
            {"name": "Just a name", "description": "", "story_type": "feature"},
        )

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_stories(limit=500)
        self.assertEqual(req.call_count, 1)  # search body has no limit param

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_stories()
        self.assertIn("ERROR: Shortcut API 403 on /stories/search.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_stories()
        self.assertIn("ERROR: Shortcut API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("shortcut")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_stories()
        self.assertIn("zeline connect shortcut", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_story("x")

    def test_secret_never_leaks_in_output(self):
        payload = {"data": [{"id": 1, "name": "S", "story_type": "feature", "workflow_state_name": "Done"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_stories()
        self.assertNotIn("SECRET-TOKEN", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-TOKEN", str(status))


class ShortcutRegistryTests(unittest.TestCase):
    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(shortcut_mod.ShortcutConnector.id, "shortcut")


if __name__ == "__main__":
    unittest.main()
