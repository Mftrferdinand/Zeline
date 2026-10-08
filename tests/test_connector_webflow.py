"""Tests for the Webflow connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import webflow as webflow_mod
from zeline.connectors.webflow import WebflowConnector

API_BASE = "https://api.webflow.com/v2"


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

    store.save("webflow", {"api_token": "wf_token-test"})


class WebflowConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-webflow-test-"))
        _patch_store(self, self.tmp)
        self.conn = WebflowConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"id": "u1"})) as get:
            result = self.conn.connect(api_token="wf_token-test")
        self.assertEqual(result, "Connected to Webflow.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/token/authorized_by")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer wf_token-test"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("webflow"), {"api_token": "wf_token-test"})

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            result = self.conn.connect(api_token=bad)
            self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("webflow"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_token="wf_token-test")
        self.assertTrue(result.startswith("ERROR: could not reach Webflow"))
        self.assertIsNone(store.load("webflow"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_token="bad")
        self.assertTrue(result.startswith("ERROR: Webflow rejected the API token"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("webflow"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "Webflow API v2"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Webflow disconnected.")
        self.assertEqual(self.conn.disconnect(), "Webflow was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "webflow")
        self.assertEqual(self.conn.name, "Webflow")
        self.assertEqual(self.conn.auth_kind, "pat")


class WebflowOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-webflow-test-"))
        _patch_store(self, self.tmp)
        self.conn = WebflowConnector()
        _seed_connected()

    def test_list_sites(self):
        payload = {
            "sites": [
                {"id": "site_1", "displayName": "Acme Blog"},
                {"id": "site_2", "displayName": "Acme Store"},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_sites(limit=2)
        self.assertEqual(result, "site_1: Acme Blog\nsite_2: Acme Store")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/sites")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_sites_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"sites": []})):
            self.assertEqual(self.conn.list_sites(), "No sites found.")

    def test_list_sites_limit_clamped(self):
        payload = {"sites": [
            {"id": f"site_{i}", "displayName": f"S{i}"} for i in range(150)
        ]}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.list_sites(limit=500)
        self.assertEqual(len(result.splitlines()), 100)

    def test_list_collections(self):
        payload = {
            "collections": [
                {"id": "col_1", "displayName": "Blog Posts",
                 "fields": [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]},
                {"id": "col_2", "displayName": "Authors", "fields": []},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_collections("site_1", limit=2)
        self.assertEqual(
            result,
            "col_1: Blog Posts (3 fields)\ncol_2: Authors (0 fields)",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/sites/site_1/collections")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_collections_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"collections": []})):
            self.assertEqual(self.conn.list_collections("site_1"), "No collections found on site site_1.")

    def test_list_collections_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_collections("nope")
        self.assertIn("ERROR: Webflow API 404 on /sites/nope/collections.", str(ctx.exception))

    def test_list_sites_network_error(self):
        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_sites()
        self.assertIn("ERROR: Webflow API request failed", str(ctx.exception))

    def test_operations_disconnected_raise(self):
        from zeline.connectors import store

        store.delete("webflow")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_sites()
        self.assertIn("zeline connect webflow", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_collections("site_1")


class WebflowRegistryTests(unittest.TestCase):
    def test_webflow_registered(self):
        from zeline.connectors import get

        conn = get("webflow")
        self.assertIsInstance(conn, WebflowConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(webflow_mod.WebflowConnector.id, "webflow")


if __name__ == "__main__":
    unittest.main()
