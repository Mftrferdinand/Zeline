"""Tests for the Pipedrive connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import pipedrive as pipedrive_mod
from zeline.connectors.pipedrive import PipedriveConnector

API_BASE = "https://api.pipedrive.com/v1"


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
    from zeline.connectors import store

    store.save("pipedrive", {"api_token": "t123", "name": "Bob"})


class PipedriveConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pd-test-"))
        _patch_store(self, self.tmp)
        self.conn = PipedriveConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        body = {"success": True, "data": {"name": "Bob"}}
        with mock.patch("requests.get", return_value=FakeResponse(body)) as get:
            result = self.conn.connect("t123")
        self.assertEqual(result, "Connected to Pipedrive as Bob.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/users/me")
        self.assertEqual(kwargs["params"], {"api_token": "t123"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("pipedrive"), {"api_token": "t123", "name": "Bob"})

    def test_connect_missing_token(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect("").startswith("ERROR: no api token"))
        self.assertIsNone(store.load("pipedrive"))

    def test_connect_unsuccessful_body(self):
        from zeline.connectors import store

        body = {"success": False, "error": "bad key"}
        with mock.patch("requests.get", return_value=FakeResponse(body)):
            result = self.conn.connect("bad")
        self.assertTrue(result.startswith("ERROR: Pipedrive rejected the token"))
        self.assertIsNone(store.load("pipedrive"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect("t123")
        self.assertTrue(result.startswith("ERROR: could not reach Pipedrive"))
        self.assertIsNone(store.load("pipedrive"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("t123")
        self.assertIn("401", result)
        self.assertIsNone(store.load("pipedrive"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "Bob"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Pipedrive disconnected.")
        self.assertEqual(self.conn.disconnect(), "Pipedrive was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "pipedrive")
        self.assertEqual(self.conn.name, "Pipedrive")
        self.assertEqual(self.conn.auth_kind, "pat")


class PipedriveOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pd-test-"))
        _patch_store(self, self.tmp)
        self.conn = PipedriveConnector()
        _seed_connected()

    def _request_side_effect(self, mapping):
        def _side_effect(method, url, *args, **kwargs):
            key = (method, url)
            if key in mapping:
                payload, status = mapping[key]
                return FakeResponse(payload, status)
            raise AssertionError(f"unexpected {method} {url}")

        return _side_effect

    def test_list_deals(self):
        mapping = {
            ("GET", f"{API_BASE}/deals"): (
                {"success": True, "data": [
                    {"id": 7, "title": "Acme", "value": 5000},
                    {"id": 8, "title": "Beta", "value": 0},
                ]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_deals(limit=2)
        self.assertEqual(result, "7: Acme ($5000)\n8: Beta ($0)")
        _, kwargs = req.call_args
        self.assertEqual(kwargs["params"], {"limit": 2, "api_token": "t123"})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_deals_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"success": True, "data": []})):
            self.assertEqual(self.conn.list_deals(), "No deals found.")

    def test_list_deals_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/deals"): (
                {"success": True, "data": [{"id": i, "title": "D", "value": 1} for i in range(150)]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_deals(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"]["limit"], 100)

    def test_list_deals_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_deals()
        self.assertIn("ERROR: Pipedrive API 500", str(ctx.exception))

    def test_list_deals_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_deals()
        self.assertIn("ERROR: Pipedrive API request failed", str(ctx.exception))

    def test_create_deal(self):
        mapping = {
            ("POST", f"{API_BASE}/deals"): (
                {"success": True, "data": {"id": 42, "title": "Big"}},
                201,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.create_deal("Big", value="2500")
        self.assertEqual(result, "Deal created: 42")
        self.assertEqual(req.call_args.kwargs["json"], {"title": "Big", "value": "2500"})

    def test_create_deal_no_value(self):
        mapping = {
            ("POST", f"{API_BASE}/deals"): (
                {"success": True, "data": {"id": 43}},
                201,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            self.assertEqual(self.conn.create_deal("Small"), "Deal created: 43")
        self.assertEqual(req.call_args.kwargs["json"], {"title": "Small"})

    def test_create_deal_rejected(self):
        mapping = {
            ("POST", f"{API_BASE}/deals"): ({"success": False}, 200)
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_deal("X")
        self.assertIn("ERROR: Pipedrive rejected the deal.", str(ctx.exception))

    def test_create_deal_missing_title(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_deal("  ")
        self.assertIn("title is required", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("pipedrive")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_deals()
        self.assertIn("zeline connect pipedrive", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_deal("X")


class PipedriveRegistryTests(unittest.TestCase):
    def test_registered(self):
        from zeline.connectors import get

        self.assertIsInstance(get("pipedrive"), PipedriveConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(pipedrive_mod.PipedriveConnector.id, "pipedrive")


if __name__ == "__main__":
    unittest.main()
