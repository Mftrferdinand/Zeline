"""Tests for the Beehiiv connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import beehiiv as beehiiv_mod
from zeline.connectors.beehiiv import BeehiivConnector

API_BASE = "https://api.beehiiv.com/v2"
TOKEN = "bh_fake_api_key_123"
PUB_ID = "pub_abc123"


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

    store.save("beehiiv", {"api_key": TOKEN, "publication_id": PUB_ID})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class BeehiivConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bh-test-"))
        _patch_store(self, self.tmp)
        self.conn = BeehiivConnector()

    def test_connect_success_saves_credentials(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"data": {"id": PUB_ID, "name": "My Newsletter"}}),
        ) as get:
            result = self.conn.connect(TOKEN, PUB_ID)
        self.assertEqual(result, "Connected to Beehiiv publication My Newsletter.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/publications/{PUB_ID}")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("beehiiv"),
            {"api_key": TOKEN, "publication_id": PUB_ID},
        )

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"data": {"id": PUB_ID, "name": "N"}}),
        ):
            result = self.conn.connect(token=TOKEN, publication_id=PUB_ID)
        self.assertTrue(result.startswith("Connected to Beehiiv"))

    def test_connect_401_saves_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(TOKEN, PUB_ID)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("beehiiv"))

    def test_connect_empty_key_saves_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", PUB_ID)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("beehiiv"))
        get.assert_not_called()

    def test_connect_empty_publication_id_saves_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(TOKEN, "")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("beehiiv"))
        get.assert_not_called()

    def test_connect_network_error_saves_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN, PUB_ID)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("beehiiv"))

    def test_connect_non_json_body_does_not_crash(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN, PUB_ID)
        self.assertEqual(result, "Connected to Beehiiv publication ?.")

    def test_status_connected_shows_publication_id(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status["connected"], True)
        self.assertIn(PUB_ID, status["detail"])
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Beehiiv disconnected.")
        self.assertEqual(self.conn.disconnect(), "Beehiiv was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "beehiiv")
        self.assertEqual(self.conn.name, "Beehiiv")
        self.assertEqual(self.conn.description, "Read Beehiiv publication posts.")
        self.assertEqual(self.conn.auth_kind, "pat")


class BeehiivOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bh-test-"))
        _patch_store(self, self.tmp)
        self.conn = BeehiivConnector()
        _seed_connected()

    def test_list_posts(self):
        mapping = {
            ("GET", f"{API_BASE}/publications/{PUB_ID}/posts"): (
                {"data": [
                    {"id": "p1", "title": "First Post", "status": "confirmed",
                     "published_at": "2026-10-01T08:00:00Z"},
                    {"id": "p2", "title": "Second Post", "status": "draft"},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_posts(limit=2)
        self.assertEqual(
            result,
            "First Post [confirmed] (2026-10-01T08:00:00Z)\nSecond Post [draft] (?)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/publications/{PUB_ID}/posts")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_posts_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/publications/{PUB_ID}/posts"): (
                {"data": [{"id": f"p{i}", "title": f"T{i}", "status": "confirmed"} for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_posts(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_posts_min_limit(self):
        mapping = {("GET", f"{API_BASE}/publications/{PUB_ID}/posts"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_posts(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 1})

    def test_list_posts_empty(self):
        mapping = {("GET", f"{API_BASE}/publications/{PUB_ID}/posts"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_posts(), "No posts found.")

    def test_list_posts_http_error(self):
        mapping = {("GET", f"{API_BASE}/publications/{PUB_ID}/posts"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_posts()
        self.assertEqual(
            str(ctx.exception),
            f"ERROR: Beehiiv API 500 on /publications/{PUB_ID}/posts.",
        )

    def test_list_posts_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_posts()
        self.assertIn("ERROR: Beehiiv API request failed", str(ctx.exception))

    def test_list_posts_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_posts()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("beehiiv")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_posts()
        self.assertIn("zeline connect beehiiv", str(ctx.exception))

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_posts()
        self.assertNotIn(TOKEN, str(ctx.exception))


class BeehiivRegistryTests(unittest.TestCase):
    def test_beehiiv_registered(self):
        from zeline.connectors import get

        conn = get("beehiiv")
        self.assertIsInstance(conn, BeehiivConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(beehiiv_mod.BeehiivConnector.id, "beehiiv")


if __name__ == "__main__":
    unittest.main()
