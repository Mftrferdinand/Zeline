"""Tests for the Reddit connector (script app credentials). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import reddit as reddit_mod
from zeline.connectors.reddit import RedditConnector

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"


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

    store.save(
        "reddit",
        {
            "client_id": "CID",
            "client_secret": "SECRET-CLIENT-SECRET",
            "username": "aester",
            "password": "SECRET-PASSWORD",
        },
    )


class RedditConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-reddit-test-"))
        _patch_store(self, self.tmp)
        self.conn = RedditConnector()

    def test_connect_success_saves_credentials(self):
        from zeline.connectors import store

        fake = FakeResponse({"access_token": "T", "token_type": "bearer"})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.connect(
                client_id="CID",
                client_secret="CS",
                username="aester",
                password="pw",
            )
        self.assertEqual(result, "Connected to Reddit as u/aester.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], TOKEN_URL)
        self.assertEqual(kwargs["auth"], ("CID", "CS"))
        self.assertEqual(
            kwargs["data"],
            {"grant_type": "password", "username": "aester", "password": "pw"},
        )
        self.assertEqual(kwargs["headers"]["User-Agent"], "zeline/0.1 by aester")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("reddit")
        self.assertEqual(saved["client_id"], "CID")
        self.assertEqual(saved["client_secret"], "CS")
        self.assertEqual(saved["username"], "aester")
        self.assertEqual(saved["password"], "pw")

    def test_connect_missing_credentials_stores_nothing(self):
        from zeline.connectors import store

        base = dict(client_id="CID", client_secret="CS", username="aester", password="pw")
        with mock.patch("requests.post") as post:
            for missing in base:
                kwargs = dict(base)
                kwargs[missing] = ""
                result = self.conn.connect(**kwargs)
                self.assertTrue(result.startswith("ERROR:"), missing)
                self.assertIn("client_id, client_secret, username and password", result)
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
        post.assert_not_called()
        self.assertIsNone(store.load("reddit"))

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "invalid_grant"}, status=401)
        with mock.patch("requests.post", return_value=fake):
            result = self.conn.connect(client_id="C", client_secret="S", username="u", password="p")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("reddit"))

    def test_connect_no_access_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({})):
            result = self.conn.connect(client_id="C", client_secret="S", username="u", password="p")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("reddit"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(client_id="C", client_secret="S", username="u", password="p")
        self.assertTrue(result.startswith("ERROR: could not reach www.reddit.com"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "u/aester")
        self.assertNotIn("SECRET-CLIENT-SECRET", status["detail"])
        self.assertNotIn("SECRET-PASSWORD", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Reddit disconnected.")
        self.assertEqual(self.conn.disconnect(), "Reddit was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "reddit")
        self.assertEqual(self.conn.name, "Reddit")
        self.assertEqual(self.conn.auth_kind, "pat")


class RedditOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-reddit-test-"))
        _patch_store(self, self.tmp)
        self.conn = RedditConnector()
        _seed_connected()

    def _mock_auth(self):
        """Mock the token refresh POST; the caller patches requests.request for the API."""
        return mock.patch("requests.post", return_value=FakeResponse({"access_token": "T"}))

    def test_list_subreddit_posts(self):
        payload = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Post A",
                            "score": 123,
                            "num_comments": 45,
                            "author": "alice",
                        }
                    },
                    {
                        "data": {
                            "title": "Post B",
                            "score": 10,
                            "num_comments": 0,
                            "author": "bob",
                        }
                    },
                ]
            }
        }
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_subreddit_posts("python", sort="new", limit=2)
        self.assertEqual(
            result,
            "Post A (123 points, 45 comments, u/alice)\nPost B (10 points, 0 comments, u/bob)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/r/python/new")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer T")
        self.assertEqual(kwargs["headers"]["User-Agent"], "zeline/0.1 by aester")
        self.assertEqual(kwargs["params"]["limit"], 2)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_subreddit_posts_invalid_sort_defaults_to_hot(self):
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse({"data": {"children": []}})) as req:
            self.assertEqual(
                self.conn.list_subreddit_posts("python", sort="rising"),
                "No posts found in r/python.",
            )
        self.assertEqual(req.call_args[0][1], f"{API_BASE}/r/python/hot")

    def test_list_subreddit_posts_none(self):
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse({"data": {"children": []}})):
            self.assertEqual(
                self.conn.list_subreddit_posts("emptytest"),
                "No posts found in r/emptytest.",
            )

    def test_search_with_subreddit(self):
        payload = {
            "data": {
                "children": [
                    {"data": {"title": "Q", "score": 5, "num_comments": 2, "author": "z"}}
                ]
            }
        }
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.search("query here", subreddit="python", limit=5)
        self.assertEqual(result, "Q (5 points, 2 comments, u/z)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/search")
        params = kwargs["params"]
        self.assertEqual(params["q"], "query here")
        self.assertEqual(params["limit"], 5)
        self.assertEqual(params["restrict_sr"], "true")
        self.assertEqual(params["sr"], "python")

    def test_search_without_subreddit(self):
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse({"data": {"children": []}})) as req:
            self.assertEqual(self.conn.search("hello"), "No posts found.")
        params = req.call_args[1]["params"]
        self.assertEqual(params["q"], "hello")
        self.assertNotIn("restrict_sr", params)
        self.assertNotIn("sr", params)

    def test_limit_clamped(self):
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse({"data": {"children": []}})) as req:
            self.conn.list_subreddit_posts("python", limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse({"data": {"children": []}})) as req:
            self.conn.search("x", limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_token_refresh_uses_stored_credentials(self):
        with mock.patch("requests.post", return_value=FakeResponse({"access_token": "NEW"})) as post, \
            mock.patch("requests.request", return_value=FakeResponse({"data": {"children": []}})):
            self.conn.list_subreddit_posts("python")
        args, kwargs = post.call_args
        self.assertEqual(args[0], TOKEN_URL)
        self.assertEqual(kwargs["auth"], ("CID", "SECRET-CLIENT-SECRET"))
        self.assertEqual(kwargs["data"]["username"], "aester")
        self.assertEqual(kwargs["data"]["password"], "SECRET-PASSWORD")
        self.assertEqual(kwargs["headers"]["User-Agent"], "zeline/0.1 by aester")

    def test_token_refresh_failure_raises(self):
        with mock.patch("requests.post", return_value=FakeResponse({"error": "x"}, status=401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_subreddit_posts("python")
        self.assertIn("ERROR: Reddit rejected the credentials (HTTP 401).", str(ctx.exception))
        self.assertNotIn("SECRET-PASSWORD", str(ctx.exception))
        self.assertNotIn("SECRET-CLIENT-SECRET", str(ctx.exception))

    def test_api_http_error(self):
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_subreddit_posts("python")
        self.assertIn("ERROR: Reddit API 403 on /r/python/hot.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with self._mock_auth(), mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search("x")
        self.assertIn("ERROR: Reddit API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("reddit")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_subreddit_posts("python")
        self.assertIn("zeline connect reddit", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search("x")

    def test_secret_never_leaks_in_output(self):
        payload = {
            "data": {
                "children": [
                    {"data": {"title": "T", "score": 1, "num_comments": 1, "author": "u"}}
                ]
            }
        }
        with self._mock_auth(), mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_subreddit_posts("python")
            self.assertNotIn("SECRET-CLIENT-SECRET", out)
            self.assertNotIn("SECRET-PASSWORD", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-CLIENT-SECRET", str(status))
        self.assertNotIn("SECRET-PASSWORD", str(status))


class RedditRegistryTests(unittest.TestCase):
    def test_reddit_registered(self):
        from zeline.connectors import get

        conn = get("reddit")
        self.assertIsInstance(conn, RedditConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(reddit_mod.RedditConnector.id, "reddit")


if __name__ == "__main__":
    unittest.main()
