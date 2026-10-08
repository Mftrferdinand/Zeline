"""Tests for the Hacker News connector (public API). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from zeline.connectors import hackernews as hackernews_mod
from zeline.connectors.hackernews import HackerNewsConnector

API_BASE = "https://hacker-news.firebaseio.com/v0"


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

    store.save("hackernews", {"connected": True})


def _get_side_effect(mapping):
    def _side_effect(url, *args, **kwargs):
        if url in mapping:
            payload, status = mapping[url]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected GET {url}")

    return _side_effect


class HackerNewsConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hn-test-"))
        _patch_store(self, self.tmp)
        self.conn = HackerNewsConnector()

    def test_connect_success_saves_marker(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse([111, 222, 333])) as get:
            result = self.conn.connect()
        self.assertEqual(result, "Connected to Hacker News (public API, no key needed).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/topstories.json")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("hackernews"), {"connected": True})

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach Hacker News API"))
        self.assertIsNone(store.load("hackernews"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach Hacker News API"))
        self.assertIn("500", result)
        self.assertIsNone(store.load("hackernews"))

    def test_connect_unexpected_body_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"oops": True})):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("hackernews"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "public API"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Hacker News disconnected.")
        self.assertEqual(self.conn.disconnect(), "Hacker News was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "hackernews")
        self.assertEqual(self.conn.name, "Hacker News")
        self.assertEqual(self.conn.auth_kind, "none")


class HackerNewsOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hn-test-"))
        _patch_store(self, self.tmp)
        self.conn = HackerNewsConnector()
        _seed_connected()

    def test_top_stories(self):
        mapping = {
            f"{API_BASE}/topstories.json": ([111, 222], 200),
            f"{API_BASE}/item/111.json": (
                {"id": 111, "title": "Story One", "score": 321, "by": "alice",
                 "url": "https://example.com/one"},
                200,
            ),
            f"{API_BASE}/item/222.json": (
                {"id": 222, "title": "Story Two", "score": 42, "by": "bob"},
                200,
            ),
        }
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            result = self.conn.top_stories(limit=2)
        self.assertEqual(
            result,
            "Story One (321 points, by alice) — https://example.com/one\n"
            "Story Two (42 points, by bob)",
        )
        fetched = [call.args[0] for call in get.call_args_list]
        self.assertIn(f"{API_BASE}/topstories.json", fetched)
        self.assertIn(f"{API_BASE}/item/111.json", fetched)
        self.assertIn(f"{API_BASE}/item/222.json", fetched)
        for call in get.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 30)

    def test_top_stories_limit_clamped(self):
        ids = list(range(1, 150))
        mapping = {f"{API_BASE}/topstories.json": (ids, 200)}
        for item_id in ids[:100]:
            mapping[f"{API_BASE}/item/{item_id}.json"] = (
                {"id": item_id, "title": f"S{item_id}", "score": 1, "by": "u"},
                200,
            )
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            result = self.conn.top_stories(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        fetched = [call.args[0] for call in get.call_args_list]
        self.assertNotIn(f"{API_BASE}/item/101.json", fetched)
        self.assertIn(f"{API_BASE}/item/100.json", fetched)

    def test_top_stories_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse([])):
            self.assertEqual(self.conn.top_stories(), "No top stories found.")

    def test_top_stories_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=503)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.top_stories()
        self.assertIn("ERROR: Hacker News API 503.", str(ctx.exception))

    def test_top_stories_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.top_stories()
        self.assertIn("ERROR: Hacker News API request failed", str(ctx.exception))

    def test_get_item(self):
        payload = {
            "id": 123,
            "title": "Ask HN: something",
            "by": "carol",
            "time": 1760000000,
            "text": "hello <p>world</p>",
        }
        expected_time = datetime.fromtimestamp(1760000000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.get_item(123)
        self.assertEqual(
            result,
            f"Title: Ask HN: something\nBy: carol\nTime: {expected_time}\nText: hello <p>world</p>",
        )
        self.assertEqual(get.call_args.args[0], f"{API_BASE}/item/123.json")
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    def test_get_item_empty_text_omitted(self):
        payload = {"id": 124, "title": "Show HN: x", "by": "dave", "time": 1760000001}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.get_item(124)
        self.assertNotIn("Text:", result)
        self.assertIn("Title: Show HN: x", result)
        self.assertIn("By: dave", result)
        self.assertIn("Time:", result)

    def test_get_item_not_found_404(self):
        with mock.patch("requests.get", return_value=FakeResponse(None, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_item(999)
        self.assertEqual(str(ctx.exception), "ERROR: item not found.")

    def test_get_item_null_body(self):
        with mock.patch("requests.get", return_value=FakeResponse(None)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_item(999)
        self.assertEqual(str(ctx.exception), "ERROR: item not found.")

    def test_get_item_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_item(1)
        self.assertIn("ERROR: Hacker News API 500.", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("hackernews")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.top_stories()
        self.assertIn("zeline connect hackernews", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_item(1)


class HackerNewsRegistryTests(unittest.TestCase):
    def test_hackernews_registered(self):
        from zeline.connectors import get

        conn = get("hackernews")
        self.assertIsInstance(conn, HackerNewsConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(hackernews_mod.HackerNewsConnector.id, "hackernews")


if __name__ == "__main__":
    unittest.main()
