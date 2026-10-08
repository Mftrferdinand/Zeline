"""Tests for the Notion connector. All HTTP is mocked — no real network."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import notion as notion_mod
from zeline.connectors import store
from zeline.connectors.notion import NotionConnector


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


def _seed_connected():
    store.save("notion", {"token": "sekret_tok", "workspace": "My Workspace"})


class NotionConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-notion-test-"))
        _patch_store(self, self.tmp)
        self.conn = NotionConnector()

    def test_connect_empty_token_rejected(self):
        for bad in ("", "   ", None):
            with self.subTest(token=bad):
                result = self.conn.connect(token=bad)
                self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("notion"))

    def test_connect_network_error_stores_nothing(self):
        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="sekret_tok")
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("notion"))

    def test_connect_rejected_401_stores_nothing(self):
        fake = FakeResponse({"object": "error", "status": 401}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="bad_tok")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("notion"))

    def test_connect_success_saves_token(self):
        fake = FakeResponse({"object": "bot", "name": "My Workspace"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="sekret_tok")
        self.assertIn("Connected to Notion", result)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.notion.com/v1/users/me")
        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sekret_tok")
        self.assertEqual(headers["Notion-Version"], "2022-06-28")
        saved = store.load("notion")
        self.assertEqual(saved["token"], "sekret_tok")
        self.assertEqual(saved["workspace"], "My Workspace")

    def test_connect_does_not_leak_token(self):
        fake = FakeResponse({"object": "bot", "name": "My Workspace"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="sekret_tok")
        self.assertNotIn("sekret_tok", result)


class NotionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-notion-test-"))
        _patch_store(self, self.tmp)
        self.conn = NotionConnector()

    def test_status_not_connected(self):
        status = self.conn.status()
        self.assertFalse(status["connected"])
        self.assertNotIn("sekret_tok", str(status))

    def test_status_connected_hides_token(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertIn("My Workspace", status["detail"])
        self.assertNotIn("sekret_tok", str(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Notion disconnected.")
        self.assertFalse(self.conn.is_connected())
        self.assertEqual(self.conn.disconnect(), "Notion was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())


class NotionOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-notion-test-"))
        _patch_store(self, self.tmp)
        self.conn = NotionConnector()
        _seed_connected()

    def _mock_request(self, payload, status=200):
        return mock.patch("requests.request", return_value=FakeResponse(payload, status))

    def test_search(self):
        payload = {
            "results": [
                {
                    "object": "page",
                    "id": "page-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Sprint notes"}],
                        }
                    },
                },
                {
                    "object": "database",
                    "id": "db-1",
                    "title": [{"plain_text": "Tasks"}],
                },
            ]
        }
        with self._mock_request(payload) as req:
            result = self.conn.search("sprint", limit=10)
        self.assertIn("page: Sprint notes (page-1)", result)
        self.assertIn("database: Tasks (db-1)", result)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.notion.com/v1/search")
        self.assertEqual(kwargs["json"], {"query": "sprint", "page_size": 10})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sekret_tok")

    def test_search_empty(self):
        with self._mock_request({"results": []}):
            result = self.conn.search("nothing here")
        self.assertIn("No results", result)

    def test_search_limit_clamped(self):
        with self._mock_request({"results": []}) as req:
            self.conn.search("q", limit=999)
        self.assertEqual(req.call_args[1]["json"]["page_size"], 100)

    def test_create_page(self):
        with self._mock_request({"id": "new-page-id"}) as req:
            result = self.conn.create_page("parent-1", "My Title", "Body text")
        self.assertIn("Page created: new-page-id", result)
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://api.notion.com/v1/pages")
        body = kwargs["json"]
        self.assertEqual(body["parent"], {"page_id": "parent-1"})
        self.assertEqual(body["properties"]["title"][0]["text"]["content"], "My Title")
        self.assertIn("children", body)

    def test_create_page_no_content(self):
        with self._mock_request({"id": "new-page-id"}) as req:
            self.conn.create_page("parent-1", "My Title")
        self.assertNotIn("children", req.call_args[1]["json"])

    def test_query_database(self):
        payload = {
            "results": [
                {
                    "id": "row-1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Task A"}],
                        }
                    },
                },
                {
                    "id": "row-2",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Task B"}],
                        }
                    },
                },
            ]
        }
        with self._mock_request(payload) as req:
            result = self.conn.query_database("db-1", limit=5)
        self.assertIn("Task A (row-1)", result)
        self.assertIn("Task B (row-2)", result)
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://api.notion.com/v1/databases/db-1/query")
        self.assertEqual(kwargs["json"], {"page_size": 5})

    def test_query_database_empty(self):
        with self._mock_request({"results": []}):
            result = self.conn.query_database("db-1")
        self.assertIn("no rows", result)


class NotionErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-notion-test-"))
        _patch_store(self, self.tmp)
        self.conn = NotionConnector()
        _seed_connected()

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search("q")
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_operation_http_400(self):
        fake = FakeResponse({"object": "error", "status": 400}, status=400)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.query_database("db-1")
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))
        self.assertIn("400", str(ctx.exception))

    def test_operation_http_404_create(self):
        fake = FakeResponse({"object": "error", "status": 404}, status=404)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_page("nope", "title")
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_error_messages_do_not_leak_token(self):
        fake = FakeResponse({"object": "error", "status": 401}, status=401)
        with mock.patch("requests.request", return_value=fake):
            try:
                self.conn.search("q")
            except RuntimeError as exc:
                self.assertNotIn("sekret_tok", str(exc))
            else:
                self.fail("expected RuntimeError")


if __name__ == "__main__":
    unittest.main()
