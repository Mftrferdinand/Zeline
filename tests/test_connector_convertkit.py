"""Tests for the ConvertKit connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import convertkit as convertkit_mod
from zeline.connectors.convertkit import ConvertKitConnector

API_BASE = "https://api.convertkit.com"
SECRET = "ck_secret-test"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


class BadJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("bad json")


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("convertkit", {"api_key": SECRET})


class ConvertKitConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-convertkit-test-"))
        _patch_store(self, self.tmp)
        self.conn = ConvertKitConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        payload = {"name": "My List", "primary_email_address": "owner@example.com"}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.connect(api_key=SECRET)
        self.assertEqual(result, "Connected to ConvertKit (My List).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/v3/account")
        self.assertEqual(kwargs["params"], {"api_secret": SECRET})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("convertkit"), {"api_key": SECRET})

    def test_connect_token_alias(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"name": "My List"})):
            result = self.conn.connect(token=SECRET)
        self.assertTrue(result.startswith("Connected to ConvertKit"))
        self.assertEqual(store.load("convertkit"), {"api_key": SECRET})

    def test_connect_non_json_body_still_connects(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=BadJsonResponse(None)):
            result = self.conn.connect(api_key=SECRET)
        self.assertEqual(result, "Connected to ConvertKit (?).")
        self.assertEqual(store.load("convertkit"), {"api_key": SECRET})

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            result = self.conn.connect(api_key=bad)
            self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("convertkit"))

    def test_connect_unauthorized_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_key="bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("convertkit"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key=SECRET)
        self.assertTrue(result.startswith("ERROR: could not reach api.convertkit.com"))
        self.assertIsNone(store.load("convertkit"))

    def test_connect_error_message_leaks_no_secret(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_key=SECRET)
        self.assertNotIn(SECRET, result)

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "ConvertKit disconnected.")
        self.assertEqual(self.conn.disconnect(), "ConvertKit was not connected.")

    def test_status_connected_has_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status, {"connected": True, "detail": "API secret stored"})
        self.assertNotIn(SECRET, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "convertkit")
        self.assertEqual(self.conn.name, "ConvertKit")
        self.assertEqual(self.conn.description, "Read ConvertKit subscribers.")
        self.assertEqual(self.conn.auth_kind, "pat")


class ConvertKitOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-convertkit-test-"))
        _patch_store(self, self.tmp)
        self.conn = ConvertKitConnector()
        _seed_connected()

    def test_list_subscribers(self):
        payload = {
            "total_subscribers": 2,
            "subscribers": [
                {"email_address": "a@example.com", "first_name": "Alice", "state": "active"},
                {"email_address": "b@example.com", "first_name": "Bob", "state": "cancelled"},
            ],
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_subscribers(limit=2)
        self.assertEqual(
            result,
            "a@example.com - Alice (active)\nb@example.com - Bob (cancelled)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args, ("GET", f"{API_BASE}/v3/subscribers"))
        self.assertEqual(kwargs["params"], {"api_secret": SECRET, "page_size": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_subscribers_missing_name(self):
        payload = {
            "total_subscribers": 1,
            "subscribers": [{"email_address": "c@example.com", "state": "active"}],
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            result = self.conn.list_subscribers()
        self.assertEqual(result, "c@example.com - (no name) (active)")

    def test_list_subscribers_empty(self):
        payload = {"total_subscribers": 0, "subscribers": []}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.list_subscribers(), "No subscribers found.")

    def test_list_subscribers_limit_clamped(self):
        subs = [
            {"email_address": f"u{i}@example.com", "first_name": f"U{i}", "state": "active"}
            for i in range(150)
        ]
        payload = {"total_subscribers": 150, "subscribers": subs}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_subscribers(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"api_secret": SECRET, "page_size": 100})

    def test_list_subscribers_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_subscribers()
        self.assertIn("ERROR: ConvertKit API 500 on /v3/subscribers.", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_list_subscribers_network_error(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_subscribers()
        self.assertIn("ERROR: ConvertKit API request failed", str(ctx.exception))

    def test_operations_disconnected_raise(self):
        from zeline.connectors import store

        store.delete("convertkit")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_subscribers()
        self.assertIn("zeline connect convertkit", str(ctx.exception))


class ConvertKitRegistryTests(unittest.TestCase):
    def test_convertkit_registered(self):
        from zeline.connectors import get

        conn = get("convertkit")
        self.assertIsInstance(conn, ConvertKitConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(convertkit_mod.ConvertKitConnector.id, "convertkit")


if __name__ == "__main__":
    unittest.main()
