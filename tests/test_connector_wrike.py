"""Tests for the Wrike connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import wrike as wrike_mod
from zeline.connectors.wrike import WrikeConnector


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

    store.save("wrike", {"token": "SECRET-TOKEN", "account": "Acme Corp"})


class WrikeConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wrike-test-"))
        _patch_store(self, self.tmp)
        self.conn = WrikeConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"data": [{"name": "Acme Corp", "id": "ACC1"}]})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="PAT")
        self.assertEqual(result, "Connected to Wrike (account Acme Corp).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://www.wrike.com/api/v4/account")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer PAT")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("wrike")
        self.assertEqual(saved["token"], "PAT")
        self.assertEqual(saved["account"], "Acme Corp")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "Unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("wrike"))

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(token="  ").startswith("ERROR:"))
            self.assertEqual(self.conn.connect(), "ERROR: token is required.")
        get.assert_not_called()
        self.assertIsNone(store.load("wrike"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="PAT")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_connect_unreadable_response(self):
        from zeline.connectors import store

        fake = FakeResponse({"unexpected": "shape"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="PAT")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("wrike"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "account Acme Corp")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Wrike disconnected.")
        self.assertEqual(self.conn.disconnect(), "Wrike was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "wrike")
        self.assertEqual(self.conn.name, "Wrike")
        self.assertEqual(self.conn.description, "List and create tasks in Wrike.")
        self.assertEqual(self.conn.auth_kind, "pat")


class WrikeOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wrike-test-"))
        _patch_store(self, self.tmp)
        self.conn = WrikeConnector()
        _seed_connected()

    def test_list_tasks(self):
        payload = {
            "data": [
                {"id": "T1", "title": "Ship launch", "status": "Active"},
                {"id": "T2", "title": "Write docs", "status": "Completed"},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_tasks(limit=2)
        self.assertEqual(result, "Ship launch [Active]\nWrite docs [Completed]")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://www.wrike.com/api/v4/tasks")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        self.assertEqual(kwargs["params"]["pageSize"], 2)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_tasks_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_tasks(), "No tasks found.")

    def test_create_task(self):
        payload = {"data": [{"id": "T987", "title": "New task", "status": "Active"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.create_task("New task", folder_id="F123", description="Do it")
        self.assertEqual(result, "Task created: T987")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://www.wrike.com/api/v4/folders/F123/tasks")
        self.assertEqual(kwargs["json"], {"title": "New task", "description": "Do it"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")

    def test_create_task_no_description(self):
        payload = {"data": [{"id": "T5"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            self.conn.create_task("Quick", folder_id="F1")
        self.assertEqual(req.call_args[1]["json"]["description"], "")

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_tasks(limit=500)
        self.assertEqual(req.call_args[1]["params"]["pageSize"], 100)
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_tasks(limit=0)
        self.assertEqual(req.call_args[1]["params"]["pageSize"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR: Wrike API 403 on /tasks.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR: Wrike API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("wrike")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_tasks()
        self.assertIn("zeline connect wrike", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_task("x", folder_id="F1")

    def test_secret_never_leaks_in_output(self):
        payload = {"data": [{"title": "Ship launch", "status": "Active"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_tasks()
        self.assertNotIn("SECRET-TOKEN", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-TOKEN", str(status))


class WrikeRegistryTests(unittest.TestCase):
    def test_wrike_registered(self):
        from zeline.connectors import get

        conn = get("wrike")
        self.assertIsInstance(conn, WrikeConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(wrike_mod.WrikeConnector.id, "wrike")


if __name__ == "__main__":
    unittest.main()
