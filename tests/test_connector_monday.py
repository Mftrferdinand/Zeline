"""Tests for the monday.com connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.monday import MondayConnector


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

    store.save("monday", {"token": "TOK", "user": "aes"})


class MondayConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-monday-test-"))
        _patch_store(self, self.tmp)
        self.conn = MondayConnector()

    def test_connect_bad_token_errors_and_saves_nothing(self):
        fake = FakeResponse({"data": {"me": None}}, status=200)
        with mock.patch("requests.post", return_value=fake) as post, \
                mock.patch("zeline.connectors.store.save") as save:
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        self.assertFalse(self.conn.status()["connected"])

    def test_connect_http_rejected(self):
        fake = FakeResponse({"message": "Unauthorized"}, status=401)
        with mock.patch("requests.post", return_value=fake), \
                mock.patch("zeline.connectors.store.save") as save:
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token=" ").startswith("ERROR:"))

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"data": {"me": {"name": "aes"}}})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.connect(token="TOK")
        self.assertEqual(result, "Connected to monday.com as aes.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.monday.com/v2")
        self.assertEqual(kwargs["json"], {"query": "{ me { name } }"})
        self.assertEqual(kwargs["headers"], {"Authorization": "TOK"})
        saved = store.load("monday")
        self.assertEqual(saved["token"], "TOK")
        self.assertEqual(saved["user"], "aes")

    def test_connect_unreachable(self):
        with mock.patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")):
            result = self.conn.connect(token="TOK")
        self.assertTrue(result.startswith("ERROR:"))


class MondayStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-monday-test-"))
        _patch_store(self, self.tmp)
        self.conn = MondayConnector()

    def test_status_disconnected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "aes")
        self.assertNotIn("TOK", repr(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "monday.com disconnected.")
        self.assertEqual(self.conn.disconnect(), "monday.com was not connected.")


class MondayOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-monday-test-"))
        _patch_store(self, self.tmp)
        self.conn = MondayConnector()
        _seed_connected()

    def test_list_boards(self):
        payload = {"data": {"boards": [
            {"id": "111", "name": "Sprint"},
            {"id": "222", "name": "Backlog"},
        ]}}
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            out = self.conn.list_boards(limit=2)
        self.assertEqual(out, "111: Sprint\n222: Backlog")
        query = post.call_args[1]["json"]["query"]
        self.assertIn("limit: 2", query)

    def test_list_boards_limit_clamped(self):
        with mock.patch("requests.post", return_value=FakeResponse({"data": {"boards": []}})) as post:
            self.conn.list_boards(limit=500)
        self.assertIn("limit: 100", post.call_args[1]["json"]["query"])

    def test_list_boards_empty(self):
        with mock.patch("requests.post", return_value=FakeResponse({"data": {"boards": []}})):
            out = self.conn.list_boards()
        self.assertEqual(out, "No boards found.")

    def test_list_items(self):
        payload = {"data": {"boards": [{
            "items_page": {"items": [
                {"id": "1", "name": "Task A"},
                {"id": "2", "name": "Task B"},
            ]}
        }]}}
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            out = self.conn.list_items("111", limit=2)
        self.assertEqual(out, "1: Task A\n2: Task B")
        query = post.call_args[1]["json"]["query"]
        self.assertIn("ids: 111", query)

    def test_list_items_empty_board_id(self):
        self.assertTrue(self.conn.list_items("").startswith("ERROR:"))

    def test_list_items_empty(self):
        payload = {"data": {"boards": [{"items_page": {"items": []}}]}}
        with mock.patch("requests.post", return_value=FakeResponse(payload)):
            out = self.conn.list_items("111")
        self.assertEqual(out, "No items in board 111.")

    def test_gql_errors_raise(self):
        payload = {"errors": [{"message": "bad query"}]}
        with mock.patch("requests.post", return_value=FakeResponse(payload)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_boards()
        self.assertIn("bad query", str(ctx.exception))
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_http_error_raises(self):
        with mock.patch("requests.post", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError):
                self.conn.list_boards()

    def test_not_connected_raises(self):
        from zeline.connectors import store

        store.delete("monday")
        with self.assertRaises(RuntimeError):
            self.conn.list_boards()


if __name__ == "__main__":
    unittest.main()
