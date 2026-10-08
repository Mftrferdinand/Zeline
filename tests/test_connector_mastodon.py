"""Tests for the Mastodon connector (access token, any instance). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import mastodon as mastodon_mod
from zeline.connectors.mastodon import MastodonConnector


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
        "mastodon",
        {
            "access_token": "SECRET-TOKEN",
            "instance": "https://mastodon.social",
            "username": "aester",
        },
    )


class MastodonConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-mastodon-test-"))
        _patch_store(self, self.tmp)
        self.conn = MastodonConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "1", "username": "aester", "acct": "aester"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(access_token="PAT", instance="mastodon.social")
        self.assertEqual(result, "Connected to Mastodon as @aester on https://mastodon.social.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://mastodon.social/api/v1/accounts/verify_credentials")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer PAT")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("mastodon")
        self.assertEqual(saved["access_token"], "PAT")
        self.assertEqual(saved["instance"], "https://mastodon.social")
        self.assertEqual(saved["username"], "aester")

    def test_connect_instance_normalized(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "1", "username": "ops", "acct": "ops"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(access_token="PAT", instance="https://mstdn.example/")
        self.assertIn("https://mstdn.example.", result)
        self.assertEqual(store.load("mastodon")["instance"], "https://mstdn.example")

    def test_connect_instance_keeps_explicit_scheme(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "1", "username": "ops", "acct": "ops"})
        with mock.patch("requests.get", return_value=fake):
            self.conn.connect(access_token="PAT", instance="http://localhost:3000")
        self.assertEqual(store.load("mastodon")["instance"], "http://localhost:3000")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "The access token is invalid"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(access_token="BAD", instance="mastodon.social")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("mastodon"))

    def test_connect_empty_credential_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect(instance="mastodon.social").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(access_token="  ", instance="mastodon.social").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(access_token="PAT", instance="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("mastodon"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(access_token="PAT", instance="mastodon.social")
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIn("mastodon.social", result)

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@aester on https://mastodon.social")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Mastodon disconnected.")
        self.assertEqual(self.conn.disconnect(), "Mastodon was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "mastodon")
        self.assertEqual(self.conn.name, "Mastodon")
        self.assertEqual(self.conn.description, "Post toots and read timelines on any Mastodon instance.")
        self.assertEqual(self.conn.auth_kind, "pat")


class MastodonOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-mastodon-test-"))
        _patch_store(self, self.tmp)
        self.conn = MastodonConnector()
        _seed_connected()

    def test_post_toot(self):
        fake = FakeResponse({"id": "1", "url": "https://mastodon.social/@aester/1"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.post_toot("hello fediverse")
        self.assertEqual(result, "Toot posted: https://mastodon.social/@aester/1")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://mastodon.social/api/v1/statuses")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        self.assertEqual(kwargs["json"], {"status": "hello fediverse", "visibility": "public"})
        self.assertEqual(kwargs["timeout"], 30)

    def test_post_toot_custom_visibility(self):
        fake = FakeResponse({"id": "2", "url": "https://mastodon.social/@aester/2"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.post_toot("followers only", visibility="unlisted")
        self.assertEqual(result, "Toot posted: https://mastodon.social/@aester/2")
        self.assertEqual(req.call_args[1]["json"]["visibility"], "unlisted")

    def test_read_timeline(self):
        payload = [
            {
                "id": "1",
                "content": "<p>Hello <b>world</b> &amp; friends</p>",
                "account": {"display_name": "Aester", "acct": "aester"},
            },
            {
                "id": "2",
                "content": "<p>Second <a href=\"https://x.example\">link</a></p>",
                "account": {"display_name": "", "acct": "ops"},
            },
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.read_timeline(limit=2)
        self.assertEqual(
            result,
            "Aester (@aester): Hello world &amp; friends\nops (@ops): Second link",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://mastodon.social/api/v1/timelines/home")
        self.assertEqual(kwargs["params"]["limit"], 2)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")

    def test_read_timeline_none(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.read_timeline(), "No toots found.")

    def test_read_timeline_truncates_long_toots(self):
        payload = [
            {
                "id": "1",
                "content": "<p>" + "x" * 300 + "</p>",
                "account": {"display_name": "Aester", "acct": "aester"},
            }
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            result = self.conn.read_timeline()
        self.assertEqual(result, "Aester (@aester): " + "x" * 200)

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.read_timeline(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.read_timeline(limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.post_toot("hi")
        self.assertIn("ERROR: Mastodon API 403 on /api/v1/statuses.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.read_timeline()
        self.assertIn("ERROR: Mastodon API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("mastodon")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.post_toot("hi")
        self.assertIn("zeline connect mastodon", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.read_timeline()

    def test_secret_never_leaks_in_output(self):
        payload = [
            {
                "id": "1",
                "content": "<p>plain</p>",
                "account": {"display_name": "Aester", "acct": "aester"},
            }
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.read_timeline()
        self.assertNotIn("SECRET-TOKEN", out)
        self.assertNotIn("SECRET-TOKEN", str(self.conn.status()))


class MastodonRegistryTests(unittest.TestCase):
    def test_mastodon_registered(self):
        from zeline.connectors import get

        conn = get("mastodon")
        self.assertIsInstance(conn, MastodonConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(mastodon_mod.MastodonConnector.id, "mastodon")


if __name__ == "__main__":
    unittest.main()
