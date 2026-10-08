"""Tests for the Bluesky connector (app password). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import bluesky as bluesky_mod
from zeline.connectors.bluesky import BlueskyConnector


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
        "bluesky",
        {
            "identifier": "alice.bsky.social",
            "app_password": "SECRET-APP-PASSWORD",
            "did": "did:plc:alice",
            "handle": "alice.bsky.social",
        },
    )


def _session_response():
    return FakeResponse({"did": "did:plc:alice", "handle": "alice.bsky.social", "accessJwt": "JWT-TOKEN"})


class BlueskyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bluesky-test-"))
        _patch_store(self, self.tmp)
        self.conn = BlueskyConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=_session_response()) as post:
            result = self.conn.connect(identifier="alice.bsky.social", app_password="APP-PW")
        self.assertEqual(result, "Connected to Bluesky as @alice.bsky.social.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://bsky.social/xrpc/com.atproto.server.createSession")
        self.assertEqual(
            kwargs["json"], {"identifier": "alice.bsky.social", "password": "APP-PW"}
        )
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("bluesky")
        self.assertEqual(saved["identifier"], "alice.bsky.social")
        self.assertEqual(saved["app_password"], "APP-PW")
        self.assertEqual(saved["did"], "did:plc:alice")
        self.assertEqual(saved["handle"], "alice.bsky.social")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({"error": "AuthRequired"}, status=401)):
            result = self.conn.connect(identifier="alice.bsky.social", app_password="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("bluesky"))

    def test_connect_empty_credentials_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post") as post:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(identifier="a").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(app_password="p").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(identifier="  ", app_password="  ").startswith("ERROR:"))
        post.assert_not_called()
        self.assertIsNone(store.load("bluesky"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(identifier="a", app_password="p")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_connect_unreadable_response(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad json")
        with mock.patch("requests.post", return_value=fake):
            result = self.conn.connect(identifier="a", app_password="p")
        self.assertTrue(result.startswith("ERROR:"))

    def test_connect_missing_did(self):
        fake = FakeResponse({"handle": "alice.bsky.social"})
        with mock.patch("requests.post", return_value=fake):
            result = self.conn.connect(identifier="a", app_password="p")
        self.assertTrue(result.startswith("ERROR:"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@alice.bsky.social")
        self.assertNotIn("SECRET-APP-PASSWORD", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Bluesky disconnected.")
        self.assertEqual(self.conn.disconnect(), "Bluesky was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "bluesky")
        self.assertEqual(self.conn.name, "Bluesky")
        self.assertEqual(self.conn.description, "Post and read timelines on Bluesky (app password).")
        self.assertEqual(self.conn.auth_kind, "pat")


class BlueskyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bluesky-test-"))
        _patch_store(self, self.tmp)
        self.conn = BlueskyConnector()
        _seed_connected()

    def _patch_session(self):
        return mock.patch("requests.post", side_effect=[_session_response(), _session_response()])

    def test_post(self):
        payload = {"uri": "at://did:plc:alice/app.bsky.feed.post/abc", "cid": "cid1"}
        with mock.patch("requests.post", side_effect=[_session_response(), FakeResponse(payload)]) as post:
            result = self.conn.post("Hello world")
        self.assertEqual(result, "Post published: at://did:plc:alice/app.bsky.feed.post/abc")
        # second call is the createRecord request
        args, kwargs = post.call_args_list[1]
        self.assertEqual(args[0], "https://bsky.social/xrpc/com.atproto.repo.createRecord")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer JWT-TOKEN")
        body = kwargs["json"]
        self.assertEqual(body["repo"], "did:plc:alice")
        self.assertEqual(body["collection"], "app.bsky.feed.post")
        self.assertEqual(body["record"]["$type"], "app.bsky.feed.post")
        self.assertEqual(body["record"]["text"], "Hello world")
        self.assertTrue(body["record"]["createdAt"].endswith("Z"))

    def test_post_http_error(self):
        with mock.patch(
            "requests.post", side_effect=[_session_response(), FakeResponse({}, status=400)]
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.post("Hello")
        self.assertIn("ERROR:", str(ctx.exception))

    def test_post_network_error(self):
        import requests

        with mock.patch(
            "requests.post",
            side_effect=[_session_response(), requests.Timeout("slow")],
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.post("Hello")
        self.assertIn("ERROR: Bluesky API request failed", str(ctx.exception))

    def test_read_timeline(self):
        payload = {
            "feed": [
                {
                    "post": {
                        "author": {"handle": "bob.bsky.social"},
                        "record": {"text": "First post"},
                    }
                },
                {
                    "post": {
                        "author": {"handle": "carol.bsky.social"},
                        "record": {"text": "Second post"},
                    }
                },
            ]
        }
        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
                result = self.conn.read_timeline(limit=5)
        self.assertEqual(result, "@bob.bsky.social: First post\n@carol.bsky.social: Second post")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://bsky.social/xrpc/app.bsky.feed.getTimeline")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer JWT-TOKEN")
        self.assertEqual(kwargs["params"], {"limit": 5})

    def test_read_timeline_truncates_and_empties(self):
        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", return_value=FakeResponse({"feed": []})):
                self.assertEqual(self.conn.read_timeline(), "No posts in timeline.")

    def test_read_timeline_text_truncated_at_200(self):
        long_text = "x" * 250
        payload = {
            "feed": [{"post": {"author": {"handle": "bob"}, "record": {"text": long_text}}}]
        }
        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", return_value=FakeResponse(payload)):
                result = self.conn.read_timeline()
        self.assertEqual(result, f"@bob: {'x' * 200}")

    def test_limit_clamped(self):
        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", return_value=FakeResponse({"feed": []})) as req:
                self.conn.read_timeline(limit=500)
            self.assertEqual(req.call_args[1]["params"]["limit"], 100)
            with mock.patch("requests.request", return_value=FakeResponse({"feed": []})) as req:
                self.conn.read_timeline(limit=0)
            self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", return_value=FakeResponse({}, status=401)) as req:
                with self.assertRaises(RuntimeError) as ctx:
                    self.conn.read_timeline()
        self.assertIn(
            "ERROR: Bluesky API 401 on /xrpc/app.bsky.feed.getTimeline.", str(ctx.exception)
        )

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
                with self.assertRaises(RuntimeError) as ctx:
                    self.conn.read_timeline()
        self.assertIn("ERROR: Bluesky API request failed", str(ctx.exception))

    def test_session_failure_raises(self):
        with mock.patch(
            "requests.post", return_value=FakeResponse({"error": "AuthRequired"}, status=401)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.read_timeline()
        self.assertIn("ERROR: Bluesky session rejected (HTTP 401).", str(ctx.exception))

    def test_session_unreadable(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad json")
        with mock.patch("requests.post", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.read_timeline()
        self.assertIn("ERROR:", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("bluesky")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.read_timeline()
        self.assertIn("zeline connect bluesky", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.post("Hello")

    def test_secret_never_leaks_in_output(self):
        payload = {"feed": []}
        with mock.patch("requests.post", return_value=_session_response()):
            with mock.patch("requests.request", return_value=FakeResponse(payload)):
                out = self.conn.read_timeline()
        self.assertNotIn("SECRET-APP-PASSWORD", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-APP-PASSWORD", str(status))

    def test_now_iso_format(self):
        iso = self.conn._now_iso()
        self.assertTrue(iso.endswith("Z"))
        self.assertNotIn("+00:00", iso)


class BlueskyRegistryTests(unittest.TestCase):
    def test_bluesky_registered(self):
        from zeline.connectors import get

        conn = get("bluesky")
        self.assertIsInstance(conn, BlueskyConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(bluesky_mod.BlueskyConnector.id, "bluesky")


if __name__ == "__main__":
    unittest.main()
