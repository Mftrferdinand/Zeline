"""Tests for the Asana connector (personal access token, Bearer auth). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.asana import AsanaConnector

BASE = "https://app.asana.com/api/1.0"
TOKEN = "SECRET-PAT"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("asana", {"token": TOKEN, "user": "Ops Bot"})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-asana-test-"))
        _patch_store(self, self.tmp)
        self.conn = AsanaConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"data": {"gid": "1", "name": "Ops Bot"}})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Asana as Ops Bot.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/users/me")
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {TOKEN}"})
        saved = store.load("asana")
        self.assertEqual(saved["token"], TOKEN)
        self.assertEqual(saved["user"], "Ops Bot")

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"errors": [{"message": "Not Authorized"}]}, status=401)
        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            with mock.patch("requests.get", return_value=fake):
                result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        self.assertIsNone(store.load("asana"))

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="  ").startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token=TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "Ops Bot")
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Asana disconnected.")
        self.assertEqual(self.conn.disconnect(), "Asana was not connected.")


class ListTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-asana-test-"))
        _patch_store(self, self.tmp)
        self.conn = AsanaConnector()
        _seed_connected(self.tmp)

    def _payload(self):
        return {
            "data": [
                {"gid": "1", "name": "ship it", "due_on": "2026-10-10", "completed": False},
                {"gid": "2", "name": "fix bug", "due_on": None, "completed": True},
            ]
        }

    def test_list_tasks_formats_lines(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._payload())) as req:
            result = self.conn.list_tasks()
        self.assertEqual(result, "ship it [2026-10-10] (open)\nfix bug [no due date] (done)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/tasks")
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {TOKEN}"})
        params = kwargs["params"]
        self.assertEqual(params["assignee"], "me")
        self.assertEqual(params["limit"], 10)
        self.assertEqual(params["opt_fields"], "name,due_on,completed")

    def test_list_tasks_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_tasks(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_tasks(limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_list_tasks_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_tasks(), "No tasks found.")

    def test_list_tasks_api_error(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"errors": [{"message": "bad"}]}, status=400),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR: Asana API 400", str(ctx.exception))


class CreateTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-asana-test-"))
        _patch_store(self, self.tmp)
        self.conn = AsanaConnector()
        _seed_connected(self.tmp)

    def test_create_task_returns_gid(self):
        fake = FakeResponse({"data": {"gid": "1234", "name": "do it"}})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_task("do it", notes="details", workspace="ws-1")
        self.assertEqual(result, "Created task 1234.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE}/tasks")
        self.assertEqual(
            kwargs["json"],
            {"data": {"name": "do it", "notes": "details", "workspace": "ws-1"}},
        )

    def test_create_task_minimal_omits_optionals(self):
        fake = FakeResponse({"data": {"gid": "5678", "name": "simple"}})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_task("simple")
        self.assertEqual(result, "Created task 5678.")
        self.assertEqual(req.call_args[1]["json"], {"data": {"name": "simple"}})

    def test_create_task_missing_name(self):
        self.assertTrue(self.conn.create_task("").startswith("ERROR:"))
        self.assertTrue(self.conn.create_task("   ").startswith("ERROR:"))

    def test_create_task_api_error(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"errors": [{"message": "nope"}]}, status=400),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_task("do it")
        self.assertIn("ERROR: Asana API 400", str(ctx.exception))

    def test_create_task_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("asana")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_task("do it")
        self.assertIn("zeline connect asana", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
