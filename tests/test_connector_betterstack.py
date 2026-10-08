"""Tests for the Better Stack connector (API token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import betterstack as betterstack_mod
from zeline.connectors.betterstack import BetterStackConnector

API_BASE = "https://uptime.betterstack.com/api/v2"
TOKEN = "bs_fake_token_123"


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

    store.save("betterstack", {"api_token": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class BetterStackConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bs-test-"))
        _patch_store(self, self.tmp)
        self.conn = BetterStackConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"data": []}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Better Stack.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/monitors")
        self.assertEqual(kwargs["params"], {"per_page": 1})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("betterstack"), {"api_token": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Better Stack.")

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("betterstack"))
        get.assert_not_called()

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("betterstack"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("betterstack"))

    def test_connect_non_json_200_still_saves(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Better Stack.")
        self.assertEqual(store.load("betterstack"), {"api_token": TOKEN})

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status, {"connected": True, "detail": "API token stored"})
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Better Stack disconnected.")
        self.assertEqual(self.conn.disconnect(), "Better Stack was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "betterstack")
        self.assertEqual(self.conn.name, "Better Stack")
        self.assertEqual(self.conn.auth_kind, "pat")


class BetterStackOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bs-test-"))
        _patch_store(self, self.tmp)
        self.conn = BetterStackConnector()
        _seed_connected()

    def test_list_monitors(self):
        mapping = {
            ("GET", f"{API_BASE}/monitors"): (
                {"data": [
                    {"id": "m1", "attributes": {"url": "https://a.example.com", "status": "up"}},
                    {"id": "m2", "attributes": {"url": "https://b.example.com", "status": "down"}},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_monitors(limit=2)
        self.assertEqual(
            result,
            "m1: https://a.example.com [up]\nm2: https://b.example.com [down]",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/monitors")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_monitors_limit_clamped(self):
        mapping = {("GET", f"{API_BASE}/monitors"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_monitors(limit=500)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 100})
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_monitors(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 1})

    def test_list_monitors_empty(self):
        mapping = {("GET", f"{API_BASE}/monitors"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_monitors(), "No monitors found.")

    def test_list_monitors_http_error(self):
        mapping = {("GET", f"{API_BASE}/monitors"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertEqual(str(ctx.exception), "ERROR: Better Stack API 500 on /monitors.")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_list_monitors_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertIn("ERROR: Better Stack API request failed", str(ctx.exception))

    def test_list_monitors_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("betterstack")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_monitors()
        self.assertIn("zeline connect betterstack", str(ctx.exception))


class BetterStackRegistryTests(unittest.TestCase):
    def test_betterstack_registered(self):
        from zeline.connectors import get

        conn = get("betterstack")
        self.assertIsInstance(conn, BetterStackConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(betterstack_mod.BetterStackConnector.id, "betterstack")


if __name__ == "__main__":
    unittest.main()
