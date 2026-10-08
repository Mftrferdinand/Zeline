"""Tests for the Close connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from requests.auth import HTTPBasicAuth

from zeline.connectors import close as close_mod
from zeline.connectors.close import CloseConnector

API_BASE = "https://api.close.com/api/v1"


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

    store.save("close", {"api_key": "ck1", "email": "rep@co.com"})


class CloseConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-close-test-"))
        _patch_store(self, self.tmp)
        self.conn = CloseConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"email": "rep@co.com"})) as get:
            result = self.conn.connect("ck1")
        self.assertEqual(result, "Connected to Close as rep@co.com.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/me/")
        auth = kwargs["auth"]
        self.assertIsInstance(auth, HTTPBasicAuth)
        self.assertEqual(auth.username, "ck1")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("close"), {"api_key": "ck1", "email": "rep@co.com"})

    def test_connect_missing_key(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect("").startswith("ERROR: no api key"))
        self.assertIsNone(store.load("close"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect("ck1")
        self.assertTrue(result.startswith("ERROR: could not reach Close"))
        self.assertIsNone(store.load("close"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("ck1")
        self.assertTrue(result.startswith("ERROR: Close rejected the api key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("close"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "rep@co.com"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Close disconnected.")
        self.assertEqual(self.conn.disconnect(), "Close was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "close")
        self.assertEqual(self.conn.name, "Close")
        self.assertEqual(self.conn.auth_kind, "pat")


class CloseOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-close-test-"))
        _patch_store(self, self.tmp)
        self.conn = CloseConnector()
        _seed_connected()

    def _request_side_effect(self, mapping):
        def _side_effect(method, url, *args, **kwargs):
            key = (method, url)
            if key in mapping:
                payload, status = mapping[key]
                return FakeResponse(payload, status)
            raise AssertionError(f"unexpected {method} {url}")

        return _side_effect

    def test_list_leads(self):
        mapping = {
            ("GET", f"{API_BASE}/lead/"): (
                {"data": [
                    {"id": "lead_1", "name": "Acme"},
                    {"id": "lead_2", "name": "Beta"},
                ]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_leads(limit=2)
        self.assertEqual(result, "lead_1: Acme\nlead_2: Beta")
        _, kwargs = req.call_args
        self.assertEqual(kwargs["params"], {"_limit": 2})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["auth"].username, "ck1")

    def test_list_leads_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_leads(), "No leads found.")

    def test_list_leads_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/lead/"): (
                {"data": [{"id": f"l{i}", "name": "N"} for i in range(150)]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_leads(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"_limit": 100})

    def test_list_leads_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_leads()
        self.assertIn("ERROR: Close API 500", str(ctx.exception))

    def test_list_leads_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_leads()
        self.assertIn("ERROR: Close API request failed", str(ctx.exception))

    def test_create_lead(self):
        mapping = {
            ("POST", f"{API_BASE}/lead/"): ({"id": "lead_9", "name": "Gamma"}, 201)
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.create_lead("Gamma")
        self.assertEqual(result, "Lead created: lead_9")
        self.assertEqual(req.call_args.kwargs["json"], {"name": "Gamma"})

    def test_create_lead_missing_name(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_lead("  ")
        self.assertIn("name is required", str(ctx.exception))

    def test_create_lead_no_id_returned(self):
        mapping = {
            ("POST", f"{API_BASE}/lead/"): ({"name": "Gamma"}, 201)
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_lead("Gamma")
        self.assertIn("ERROR: Close did not return a lead id.", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("close")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_leads()
        self.assertIn("zeline connect close", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_lead("X")


class CloseRegistryTests(unittest.TestCase):
    def test_registered(self):
        from zeline.connectors import get

        self.assertIsInstance(get("close"), CloseConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(close_mod.CloseConnector.id, "close")


if __name__ == "__main__":
    unittest.main()
