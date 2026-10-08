"""Tests for the X API connector (bearer token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import x_api as x_api_mod
from zeline.connectors.x_api import XApiConnector


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

    store.save("x_api", {"token": "SECRET-TOKEN", "username": "aester"})


class XApiConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-x-api-test-"))
        _patch_store(self, self.tmp)
        self.conn = XApiConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"data": {"id": "1", "username": "aester"}})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="BEARER")
        self.assertEqual(result, "Connected to X as @aester.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.twitter.com/2/users/me")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer BEARER")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("x_api")
        self.assertEqual(saved["token"], "BEARER")
        self.assertEqual(saved["username"], "aester")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"title": "Unauthorized", "status": 401}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("x_api"))

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(token="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("x_api"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="BEARER")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@aester")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "X API disconnected.")
        self.assertEqual(self.conn.disconnect(), "X API was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "x_api")
        self.assertEqual(self.conn.name, "X API")
        self.assertEqual(self.conn.description, "Post tweets and read timelines via X API v2.")
        self.assertEqual(self.conn.auth_kind, "pat")


class XApiOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-x-api-test-"))
        _patch_store(self, self.tmp)
        self.conn = XApiConnector()
        _seed_connected()

    def test_post_tweet(self):
        fake = FakeResponse({"data": {"id": "12345", "text": "hello world"}})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.post_tweet("hello world")
        self.assertEqual(result, "Tweet posted: https://x.com/i/status/12345")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.twitter.com/2/tweets")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        self.assertEqual(kwargs["json"], {"text": "hello world"})
        self.assertEqual(kwargs["timeout"], 30)

    def test_read_timeline(self):
        user = FakeResponse({"data": {"id": "u1", "username": "aester"}})
        tweets = FakeResponse(
            {
                "data": [
                    {"id": "t1", "created_at": "2026-10-08T00:00:00Z", "text": "first"},
                    {"id": "t2", "created_at": "2026-10-08T01:00:00Z", "text": "second"},
                ]
            }
        )
        with mock.patch("requests.request", side_effect=[user, tweets]) as req:
            result = self.conn.read_timeline("aester", limit=2)
        self.assertEqual(
            result,
            "2026-10-08T00:00:00Z — first\n2026-10-08T01:00:00Z — second",
        )
        first, second = req.call_args_list
        self.assertEqual(first[0][1], "https://api.twitter.com/2/users/by/username/aester")
        self.assertEqual(second[0][1], "https://api.twitter.com/2/users/u1/tweets")
        params = second[1]["params"]
        self.assertEqual(params["max_results"], 2)
        self.assertEqual(params["tweet.fields"], "created_at")

    def test_read_timeline_none(self):
        user = FakeResponse({"data": {"id": "u1", "username": "aester"}})
        tweets = FakeResponse({"data": []})
        with mock.patch("requests.request", side_effect=[user, tweets]):
            self.assertEqual(self.conn.read_timeline("aester"), "No tweets found.")

    def test_read_timeline_unknown_user(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.read_timeline("ghost")
        self.assertIn("ERROR: X API 404 on /users/by/username/ghost.", str(ctx.exception))

    def test_limit_clamped(self):
        user = FakeResponse({"data": {"id": "u1", "username": "aester"}})
        tweets = FakeResponse({"data": []})
        with mock.patch("requests.request", side_effect=[user, tweets]) as req:
            self.conn.read_timeline("aester", limit=500)
        self.assertEqual(req.call_args_list[1][1]["params"]["max_results"], 100)
        with mock.patch("requests.request", side_effect=[user, tweets]) as req:
            self.conn.read_timeline("aester", limit=0)
        self.assertEqual(req.call_args_list[1][1]["params"]["max_results"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.post_tweet("hi")
        self.assertIn("ERROR: X API 403 on /tweets.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.post_tweet("hi")
        self.assertIn("ERROR: X API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("x_api")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.post_tweet("hi")
        self.assertIn("zeline connect x_api", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.read_timeline("aester")

    def test_secret_never_leaks_in_output(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": {"id": "9", "text": "x"}})):
            out = self.conn.post_tweet("x")
        self.assertNotIn("SECRET-TOKEN", out)
        self.assertNotIn("SECRET-TOKEN", str(self.conn.status()))


class XApiRegistryTests(unittest.TestCase):
    def test_x_api_registered(self):
        from zeline.connectors import get

        conn = get("x_api")
        self.assertIsInstance(conn, XApiConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(x_api_mod.XApiConnector.id, "x_api")


if __name__ == "__main__":
    unittest.main()
