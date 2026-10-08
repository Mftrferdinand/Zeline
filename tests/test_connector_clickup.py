"""Tests for the ClickUp connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.clickup import ClickUpConnector


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("clickup", {"token": "TOK", "username": "aesdev"})


class ClickUpConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-clickup-test-"))
        _patch_store(self, self.tmp)
        self.conn = ClickUpConnector()

    def test_connect_bad_token_errors_and_saves_nothing(self):
        fake = FakeResponse({"err": "Oauth token not found"}, status=401)
        with mock.patch("requests.get", return_value=fake) as get, \
                mock.patch("zeline.connectors.store.save") as save:
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.clickup.com/api/v2/user")
        self.assertEqual(kwargs["headers"], {"Authorization": "BAD"})
        self.assertFalse(self.conn.status()["connected"])

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="   ").startswith("ERROR:"))

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"user": {"id": 1, "username": "aesdev"}})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="TOK")
        self.assertEqual(result, "Connected to ClickUp as @aesdev.")
        saved = store.load("clickup")
        self.assertEqual(saved["token"], "TOK")
        self.assertEqual(saved["username"], "aesdev")

    def test_connect_unreachable(self):
        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            result = self.conn.connect(token="TOK")
        self.assertTrue(result.startswith("ERROR:"))


class ClickUpStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-clickup-test-"))
        _patch_store(self, self.tmp)
        self.conn = ClickUpConnector()

    def test_status_disconnected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("TOK", repr(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "ClickUp disconnected.")
        self.assertEqual(self.conn.disconnect(), "ClickUp was not connected.")


class ClickUpOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-clickup-test-"))
        _patch_store(self, self.tmp)
        self.conn = ClickUpConnector()
        _seed_connected()

    def test_list_tasks(self):
        payload = {"tasks": [
            {"id": "1", "name": "Fix bug", "status": {"status": "in progress"}},
            {"id": "2", "name": "Write docs", "status": {"status": "to do"}},
        ]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            out = self.conn.list_tasks("L123", limit=2)
        self.assertEqual(out, "Fix bug [in progress]\nWrite docs [to do]")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.clickup.com/api/v2/list/L123/task")
        self.assertEqual(kwargs["headers"]["Authorization"], "TOK")

    def test_list_tasks_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"tasks": []})) as req:
            self.conn.list_tasks("L123", limit=500)
        self.assertEqual(req.call_args[1]["params"], {"limit": 100})

    def test_list_tasks_empty_list_id(self):
        self.assertTrue(self.conn.list_tasks("").startswith("ERROR:"))

    def test_list_tasks_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"tasks": []})):
            out = self.conn.list_tasks("L123")
        self.assertEqual(out, "No tasks in list L123.")

    def test_create_task(self):
        with mock.patch("requests.request", return_value=FakeResponse({"id": "abc"})) as req:
            out = self.conn.create_task("L123", "New task", description="details")
        self.assertEqual(out, "Task created: abc")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.clickup.com/api/v2/list/L123/task")
        self.assertEqual(kwargs["json"], {"name": "New task", "description": "details"})

    def test_create_task_no_description_key_when_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"id": "abc"})) as req:
            self.conn.create_task("L123", "New task")
        self.assertEqual(req.call_args[1]["json"], {"name": "New task"})

    def test_create_task_missing_args(self):
        self.assertTrue(self.conn.create_task("", "x").startswith("ERROR:"))
        self.assertTrue(self.conn.create_task("L", "").startswith("ERROR:"))

    def test_api_error_raises(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks("L123")
        self.assertIn("ERROR:", str(ctx.exception))

    def test_api_not_connected_raises(self):
        from zeline.connectors import store

        store.delete("clickup")
        with self.assertRaises(RuntimeError):
            self.conn.list_tasks("L123")

    def test_api_network_failure_raises(self):
        with mock.patch("requests.request", side_effect=requests.exceptions.Timeout("slow")):
            with self.assertRaises(RuntimeError):
                self.conn.list_tasks("L123")


if __name__ == "__main__":
    unittest.main()
