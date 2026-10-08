"""Tests for the Product Hunt connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import producthunt as producthunt_mod
from zeline.connectors.producthunt import ProductHuntConnector

API_URL = "https://api.producthunt.com/v2/api/graphql"
POSTS_PAYLOAD = {
    "data": {
        "posts": {
            "edges": [
                {
                    "node": {
                        "name": "Launch A",
                        "tagline": "Best app ever",
                        "url": "https://www.producthunt.com/posts/launch-a",
                    }
                },
                {
                    "node": {
                        "name": "Launch B",
                        "tagline": "Second best",
                        "url": "https://www.producthunt.com/posts/launch-b",
                    }
                },
            ]
        }
    }
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("producthunt", {"api_token": "tok"})


class ProductHuntConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ph-test-"))
        _patch_store(self, self.tmp)
        self.conn = ProductHuntConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        ok = {"data": {"viewer": {"user": {"id": "1"}}}}
        with mock.patch("requests.post", return_value=FakeResponse(ok)) as post:
            result = self.conn.connect(api_token="tok123")
        self.assertEqual(result, "Connected to Product Hunt.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok123")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("producthunt"), {"api_token": "tok123"})

    def test_connect_no_token_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(), "ERROR: no API token provided.")
        self.assertIsNone(store.load("producthunt"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_token="tok")
        self.assertTrue(result.startswith("ERROR: could not reach api.producthunt.com"))
        self.assertIsNone(store.load("producthunt"))

    def test_connect_rejected_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_token="bad")
        self.assertIn("ERROR: Product Hunt rejected the token (HTTP 401)", result)
        self.assertIsNone(store.load("producthunt"))

    def test_connect_graphql_errors_stores_nothing(self):
        from zeline.connectors import store

        body = {"errors": [{"message": "invalid token"}]}
        with mock.patch("requests.post", return_value=FakeResponse(body)):
            result = self.conn.connect(api_token="bad")
        self.assertTrue(result.startswith("ERROR: Product Hunt rejected the token"))
        self.assertIsNone(store.load("producthunt"))

    def test_connect_unreadable_body_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse(ValueError("nope"))):
            result = self.conn.connect(api_token="tok")
        self.assertEqual(result, "ERROR: Product Hunt returned an unreadable response.")
        self.assertIsNone(store.load("producthunt"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API token"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Product Hunt disconnected.")
        self.assertEqual(self.conn.disconnect(), "Product Hunt was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "producthunt")
        self.assertEqual(self.conn.name, "Product Hunt")
        self.assertEqual(self.conn.auth_kind, "pat")


class ProductHuntOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ph-test-"))
        _patch_store(self, self.tmp)
        self.conn = ProductHuntConnector()
        _seed_connected()

    def test_todays_hunts(self):
        import copy

        with mock.patch(
            "requests.post", return_value=FakeResponse(copy.deepcopy(POSTS_PAYLOAD))
        ) as post:
            result = self.conn.todays_hunts(limit=2)
        self.assertEqual(
            result,
            "Launch A — Best app ever (https://www.producthunt.com/posts/launch-a)\n"
            "Launch B — Second best (https://www.producthunt.com/posts/launch-b)",
        )
        args, kwargs = post.call_args
        self.assertEqual(args[0], API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["timeout"], 30)
        body = kwargs["json"]
        self.assertIn("order: RANKING", body["query"])
        self.assertEqual(body["variables"], {"first": 2})

    def test_todays_hunts_limit_clamped(self):
        import copy

        with mock.patch(
            "requests.post", return_value=FakeResponse(copy.deepcopy(POSTS_PAYLOAD))
        ) as post:
            result = self.conn.todays_hunts(limit=500)
        self.assertEqual(len(result.splitlines()), 2)
        self.assertEqual(post.call_args.kwargs["json"]["variables"], {"first": 100})

    def test_todays_hunts_empty(self):
        with mock.patch(
            "requests.post", return_value=FakeResponse({"data": {"posts": {"edges": []}}})
        ):
            self.assertEqual(self.conn.todays_hunts(), "No hunts found.")

    def test_todays_hunts_graphql_error(self):
        body = {"errors": [{"message": "bad query"}]}
        with mock.patch("requests.post", return_value=FakeResponse(body)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.todays_hunts()
        self.assertIn("ERROR: Product Hunt GraphQL error: bad query.", str(ctx.exception))

    def test_search_posts(self):
        import copy

        with mock.patch(
            "requests.post", return_value=FakeResponse(copy.deepcopy(POSTS_PAYLOAD))
        ) as post:
            result = self.conn.search_posts("ai", limit=5)
        self.assertIn("Launch A — Best app ever", result)
        self.assertIn("Launch B — Second best", result)
        body = post.call_args.kwargs["json"]
        self.assertIn("query: $q", body["query"])
        self.assertEqual(body["variables"], {"q": "ai", "first": 5})

    def test_search_posts_empty(self):
        with mock.patch(
            "requests.post", return_value=FakeResponse({"data": {"posts": {"edges": []}}})
        ):
            self.assertEqual(self.conn.search_posts("zzz"), "No posts found for 'zzz'.")

    def test_operation_http_error(self):
        with mock.patch("requests.post", return_value=FakeResponse({}, status=503)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.todays_hunts()
        self.assertIn("ERROR: Product Hunt API 503.", str(ctx.exception))

    def test_operation_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_posts("ai")
        self.assertIn("ERROR: Product Hunt API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("producthunt")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.todays_hunts()
        self.assertIn("zeline connect producthunt", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search_posts("ai")


class ProductHuntRegistryTests(unittest.TestCase):
    def test_producthunt_registered(self):
        from zeline.connectors import get

        conn = get("producthunt")
        self.assertIsInstance(conn, ProductHuntConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(producthunt_mod.ProductHuntConnector.id, "producthunt")


if __name__ == "__main__":
    unittest.main()
