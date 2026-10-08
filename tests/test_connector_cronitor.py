"""Tests for the Cronitor connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import cronitor as cronitor_mod
from zeline.connectors.cronitor import CronitorConnector

API_BASE = "https://cronitor.io/api/v3"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


class BadJsonResponse:
    status_code = 200
    text = "<html>not json</html>"

    def json(self):
        raise ValueError("No JSON could be decoded")


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("cronitor", {"api_key": "cr_key-test"})


class CronitorConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cron-test-"))
        _patch_store(self, self.tmp)
        self.conn = CronitorConnector()

    def test_connect_success_saves_creds(self):
        from zeline.connectors import store

        payload = {"monitors": [{"key": "m1", "name": "Nightly backup", "status": "ok"}]}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.connect(api_key="cr_key-test")
        self.assertEqual(result, "Connected to Cronitor (1 monitors found).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/monitors")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertIn("Bearer", kwargs["headers"]["Authorization"])
        self.assertEqual(store.load("cronitor"), {"api_key": "cr_key-test"})

    def test_connect_token_alias(self):
        from zeline.connectors import store

        payload = {"monitors": []}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.connect(token="cr_key-alias")
        self.assertTrue(result.startswith("Connected to Cronitor"))
        self.assertEqual(store.load("cronitor"), {"api_key": "cr_key-alias"})

    def test_connect_missing_key_errors(self):
        from zeline.connectors import store

        for bad in ({"api_key": ""}, {"api_key": "  "}, {}):
            result = self.conn.connect(**bad)
            self.assertTrue(result.startswith("ERROR:"))
            self.assertIn("no API key", result)
        self.assertIsNone(store.load("cronitor"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_key="bad")
        self.assertTrue(result.startswith("ERROR: Cronitor rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("cronitor"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="cr_key-test")
        self.assertTrue(result.startswith("ERROR: could not reach cronitor.io"))
        self.assertIsNone(store.load("cronitor"))

    def test_connect_non_json_body_still_connects(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=BadJsonResponse()):
            result = self.conn.connect(api_key="cr_key-test")
        self.assertTrue(result.startswith("Connected to Cronitor"))
        self.assertEqual(store.load("cronitor"), {"api_key": "cr_key-test"})

    def test_status_connected_has_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status, {"connected": True, "detail": "API key stored"})
        self.assertNotIn("cr_key-test", repr(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Cronitor disconnected.")
        self.assertEqual(self.conn.disconnect(), "Cronitor was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "cronitor")
        self.assertEqual(self.conn.name, "Cronitor")
        self.assertEqual(self.conn.auth_kind, "pat")


class CronitorOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cron-test-"))
        _patch_store(self, self.tmp)
        self.conn = CronitorConnector()
        _seed_connected()

    def test_list_monitors(self):
        payload = {
            "monitors": [
                {"key": "m1", "name": "Nightly backup", "status": "ok"},
                {"key": "m2", "name": "Invoice job", "status": "failing"},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_monitors(limit=2)
        self.assertEqual(
            result,
            "m1: Nightly backup [ok]\nm2: Invoice job [failing]",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/monitors")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_monitors_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"monitors": []})):
            self.assertEqual(self.conn.list_monitors(), "No monitors found.")

    def test_list_monitors_limit_clamped(self):
        payload = {
            "monitors": [
                {"key": f"m{i}", "name": f"Monitor {i}", "status": "ok"}
                for i in range(150)
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_monitors(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 100})

    def test_list_monitors_malformed_body(self):
        with mock.patch("requests.get", return_value=FakeResponse({"nope": True})):
            self.assertEqual(self.conn.list_monitors(), "No monitors found.")

    def test_list_monitors_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertIn("ERROR: Cronitor API 500 on /monitors.", str(ctx.exception))

    def test_list_monitors_network_error(self):
        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertIn("ERROR: Cronitor API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("cronitor")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_monitors()
        self.assertIn("zeline connect cronitor", str(ctx.exception))


class CronitorRegistryTests(unittest.TestCase):
    def test_cronitor_registered(self):
        from zeline.connectors import get

        conn = get("cronitor")
        self.assertIsInstance(conn, CronitorConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(cronitor_mod.CronitorConnector.id, "cronitor")


if __name__ == "__main__":
    unittest.main()
