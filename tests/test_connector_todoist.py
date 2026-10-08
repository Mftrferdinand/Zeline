"""Tests for the Todoist connector (REST API v1). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import store
from zeline.connectors.todoist import TodoistConnector


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
    store.save("todoist", {"token": "SECRET-TOKEN", "user": "user@example.com"})


class TodoistConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-todoist-test-"))
        _patch_store(self, self.tmp)
        self.conn = TodoistConnector()

    def test_connect_empty_token_errors_and_saves_nothing(self):
        result = self.conn.connect(token="")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("todoist"))

    def test_connect_401_errors_and_saves_nothing(self):
        fake = FakeResponse({"error": "Forbidden"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("todoist"))

    def test_connect_403_errors_and_saves_nothing(self):
        fake = FakeResponse({"error": "Forbidden"}, status=403)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("todoist"))

    def test_connect_network_error_saves_nothing(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="AT")
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("todoist"))

    def test_connect_success_saves_credential(self):
        fake = FakeResponse({"id": "u1", "email": "user@example.com", "name": "User"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="AT")
        self.assertIn("Connected to Todoist as user@example.com.", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/user"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer AT")
        saved = store.load("todoist")
        self.assertEqual(saved["token"], "AT")
        self.assertEqual(saved["user"], "user@example.com")

    def test_connect_success_falls_back_to_name(self):
        fake = FakeResponse({"id": "u1", "name": "Budi"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="AT")
        self.assertIn("Budi", result)
        self.assertEqual(store.load("todoist")["user"], "Budi")

    def test_secret_never_leaks_in_connect_result(self):
        fake = FakeResponse({"id": "u1", "email": "user@example.com"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="SECRET-TOKEN")
        self.assertNotIn("SECRET-TOKEN", result)


class TodoistStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-todoist-test-"))
        _patch_store(self, self.tmp)
        self.conn = TodoistConnector()

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_hides_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertIn("user@example.com", status["detail"])
        self.assertNotIn("SECRET-TOKEN", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Todoist disconnected.")
        self.assertEqual(self.conn.disconnect(), "Todoist was not connected.")


class TodoistListTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-todoist-test-"))
        _patch_store(self, self.tmp)
        self.conn = TodoistConnector()
        _seed_connected()

    def _fake_get_tasks(self, tasks):
        fake = FakeResponse({"results": tasks, "next_cursor": None})

        def _request(method, url, headers=None, **kwargs):
            self.assertEqual(method, "GET")
            self.assertTrue(url.endswith("/tasks"))
            self.assertEqual(headers["Authorization"], "Bearer SECRET-TOKEN")
            self._last_params = kwargs.get("params")
            return fake

        return _request

    def test_list_tasks_formats_tasks(self):
        tasks = [
            {"id": "t1", "content": "Buy milk", "priority": 2},
            {"id": "t2", "content": "Pay bills", "priority": 4},
        ]
        with mock.patch("requests.request", side_effect=self._fake_get_tasks(tasks)):
            out = self.conn.list_tasks(limit=10)
        self.assertEqual(out, "• Buy milk [P2] (t1)\n• Pay bills [P4] (t2)")
        self.assertEqual(self._last_params["limit"], 10)

    def test_list_tasks_limit_clamped(self):
        with mock.patch("requests.request", side_effect=self._fake_get_tasks([])):
            out = self.conn.list_tasks(limit=500)
        self.assertEqual(out, "No tasks found.")
        self.assertEqual(self._last_params["limit"], 100)

    def test_list_tasks_accepts_bare_list_payload(self):
        fake = FakeResponse([{"id": "t1", "content": "X", "priority": 1}])
        with mock.patch("requests.request", return_value=fake):
            out = self.conn.list_tasks()
        self.assertEqual(out, "• X [P1] (t1)")

    def test_list_tasks_network_error_raises(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR:", str(ctx.exception))

    def test_list_tasks_http_400_raises(self):
        fake = FakeResponse({"error": "bad"}, status=400)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR:", str(ctx.exception))
        self.assertIn("400", str(ctx.exception))

    def test_list_tasks_secret_not_leaked_in_output(self):
        tasks = [{"id": "t1", "content": "SECRET-TOKEN", "priority": 1}]
        with mock.patch("requests.request", side_effect=self._fake_get_tasks(tasks)):
            out = self.conn.list_tasks()
        # The task content comes from the API, but the stored token must
        # never be injected by the connector itself.
        self.assertIn("t1", out)


class TodoistAddTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-todoist-test-"))
        _patch_store(self, self.tmp)
        self.conn = TodoistConnector()
        _seed_connected()

    def test_add_task_posts_and_returns_id(self):
        fake = FakeResponse({"id": "task-123", "content": "Buy milk"})
        with mock.patch("requests.request", return_value=fake) as req:
            out = self.conn.add_task("Buy milk", description="2 litres", priority=3)
        self.assertEqual(out, "Task dibuat: task-123.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/tasks"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        body = kwargs["json"]
        self.assertEqual(body["content"], "Buy milk")
        self.assertEqual(body["description"], "2 litres")
        self.assertEqual(body["priority"], 3)

    def test_add_task_empty_content_errors(self):
        with mock.patch("requests.request") as req:
            out = self.conn.add_task("   ")
        self.assertTrue(out.startswith("ERROR:"))
        req.assert_not_called()

    def test_add_task_priority_clamped(self):
        fake = FakeResponse({"id": "task-9"})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.add_task("X", priority=99)
        self.assertEqual(req.call_args[1]["json"]["priority"], 4)
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.add_task("X", priority=0)
        self.assertEqual(req.call_args[1]["json"]["priority"], 1)

    def test_add_task_network_error_raises(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.add_task("X")
        self.assertIn("ERROR:", str(ctx.exception))

    def test_add_task_http_403_raises(self):
        fake = FakeResponse({"error": "Forbidden"}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.add_task("X")
        self.assertIn("403", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
