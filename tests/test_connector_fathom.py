"""Tests for the Fathom connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import fathom as fathom_mod
from zeline.connectors.fathom import FathomConnector

API_BASE = "https://api.usefathom.com/v1"
TOKEN = "fathom_fake_token_123"


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

    store.save("fathom", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class FathomConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-fathom-test-"))
        _patch_store(self, self.tmp)
        self.conn = FathomConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"data": [], "meta": {"count": 0}}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Fathom.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/sites")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("fathom"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"data": []})
        ):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Fathom.")

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("fathom"))
        get.assert_not_called()

    def test_connect_http_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)) as get:
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("fathom"))
        get.assert_called_once()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertNotIn(TOKEN, result)
        self.assertIsNone(store.load("fathom"))

    def test_connect_non_json_200_does_not_crash(self):
        from zeline.connectors import store

        resp = FakeResponse("<html>ok</html>", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Fathom.")
        self.assertEqual(store.load("fathom"), {"api_key": TOKEN})

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status, {"connected": True, "detail": "API key stored"})
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Fathom disconnected.")
        self.assertEqual(self.conn.disconnect(), "Fathom was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "fathom")
        self.assertEqual(self.conn.name, "Fathom")
        self.assertEqual(self.conn.description, "Read Fathom Analytics sites.")
        self.assertEqual(self.conn.auth_kind, "pat")


class FathomOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-fathom-test-"))
        _patch_store(self, self.tmp)
        self.conn = FathomConnector()
        _seed_connected()

    def test_list_sites(self):
        mapping = {
            ("GET", f"{API_BASE}/sites"): (
                {"data": [
                    {"id": "S1", "name": "Blog", "timezone": "UTC"},
                    {"id": "S2", "name": "Shop", "default_tracking_timezone": "Asia/Jakarta"},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_sites(limit=2)
        self.assertEqual(result, "S1: Blog (UTC)\nS2: Shop (Asia/Jakarta)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/sites")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_sites_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/sites"): (
                {"data": [{"id": f"s{i}", "name": f"S{i}", "timezone": "UTC"} for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_sites(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_sites_min_limit(self):
        mapping = {("GET", f"{API_BASE}/sites"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_sites(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 1})

    def test_list_sites_empty(self):
        mapping = {("GET", f"{API_BASE}/sites"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_sites(), "No sites found.")

    def test_list_sites_missing_fields(self):
        mapping = {
            ("GET", f"{API_BASE}/sites"): ({"data": [{"id": "SX"}]}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_sites()
        self.assertEqual(result, "SX: (no name) (?)")

    def test_list_sites_http_500(self):
        mapping = {("GET", f"{API_BASE}/sites"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_sites()
        self.assertEqual(str(ctx.exception), "ERROR: Fathom API 500 on /sites.")

    def test_list_sites_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_sites()
        self.assertIn("ERROR: Fathom API request failed", str(ctx.exception))

    def test_list_sites_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_sites()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("fathom")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_sites()
        self.assertIn("zeline connect fathom", str(ctx.exception))

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_sites()
        self.assertNotIn(TOKEN, str(ctx.exception))


class FathomRegistryTests(unittest.TestCase):
    def test_fathom_registered(self):
        from zeline.connectors import get

        conn = get("fathom")
        self.assertIsInstance(conn, FathomConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(fathom_mod.FathomConnector.id, "fathom")


if __name__ == "__main__":
    unittest.main()
