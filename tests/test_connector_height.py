"""Tests for the Height connector (personal API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import height as height_mod
from zeline.connectors.height import HeightConnector


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

    store.save("height", {"api_key": "SECRET-KEY"})


class HeightConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-height-test-"))
        _patch_store(self, self.tmp)
        self.conn = HeightConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"list": []})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.connect(api_key="KEY")
        self.assertEqual(result, "Connected to Height.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.height.app/tasks/search")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer KEY")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("height")
        self.assertEqual(saved["api_key"], "KEY")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Unauthorized"}, status=401)
        with mock.patch("requests.post", return_value=fake):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("height"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post") as post:
            self.assertTrue(self.conn.connect().startswith("ERROR: api_key is required."))
            self.assertTrue(self.conn.connect(api_key="  ").startswith("ERROR:"))
        post.assert_not_called()
        self.assertIsNone(store.load("height"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-KEY", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Height disconnected.")
        self.assertEqual(self.conn.disconnect(), "Height was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "height")
        self.assertEqual(self.conn.name, "Height")
        self.assertEqual(self.conn.auth_kind, "pat")


class HeightOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-height-test-"))
        _patch_store(self, self.tmp)
        self.conn = HeightConnector()
        _seed_connected()

    def test_list_tasks(self):
        payload = {
            "list": [
                {"name": "Write release notes", "status": "inProgress"},
                {"name": "Fix flaky test", "status": "open"},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_tasks(limit=2)
        self.assertEqual(
            result,
            "Write release notes [inProgress]\nFix flaky test [open]",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.height.app/tasks/search")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-KEY")
        self.assertEqual(kwargs["json"], {"filters": {}})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_tasks_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"list": []})):
            self.assertEqual(self.conn.list_tasks(), "No tasks found.")

    def test_list_tasks_missing_fields(self):
        payload = {"list": [{}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.list_tasks(), "- [-]")

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR: Height API 403 on /tasks/search.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR: Height API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("height")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_tasks()
        self.assertIn("zeline connect height", str(ctx.exception))

    def test_secret_never_leaks_in_output(self):
        payload = {"list": [{"name": "Task", "status": "open"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_tasks()
        self.assertNotIn("SECRET-KEY", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-KEY", str(status))


class HeightRegistryTests(unittest.TestCase):
    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(height_mod.HeightConnector.id, "height")


if __name__ == "__main__":
    unittest.main()
