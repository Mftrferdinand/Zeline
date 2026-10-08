"""Tests for the Paddle connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import paddle as paddle_mod
from zeline.connectors.paddle import PaddleConnector

API_BASE = "https://api.paddle.com"


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

    store.save("paddle", {"api_key": "pdl_key-test"})


class PaddleConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-paddle-test-"))
        _patch_store(self, self.tmp)
        self.conn = PaddleConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"data": []})) as get:
            result = self.conn.connect(api_key="pdl_key-test")
        self.assertEqual(result, "Connected to Paddle.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/event-types")
        self.assertEqual(kwargs["params"], {"per_page": 1})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer pdl_key-test"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("paddle"), {"api_key": "pdl_key-test"})

    def test_connect_empty_key_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            result = self.conn.connect(api_key=bad)
            self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("paddle"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="pdl_key-test")
        self.assertTrue(result.startswith("ERROR: could not reach Paddle"))
        self.assertIsNone(store.load("paddle"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_key="bad")
        self.assertTrue(result.startswith("ERROR: Paddle rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("paddle"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "Paddle Billing API"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Paddle disconnected.")
        self.assertEqual(self.conn.disconnect(), "Paddle was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "paddle")
        self.assertEqual(self.conn.name, "Paddle")
        self.assertEqual(self.conn.auth_kind, "pat")


class PaddleOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-paddle-test-"))
        _patch_store(self, self.tmp)
        self.conn = PaddleConnector()
        _seed_connected()

    def test_list_customers(self):
        payload = {
            "data": [
                {"id": "ctm_1", "name": "Acme Inc", "email": "billing@acme.io"},
                {"id": "ctm_2", "name": "Beta LLC", "email": "pay@beta.io"},
            ],
            "meta": {"pagination": {}},
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_customers(limit=2)
        self.assertEqual(
            result,
            "ctm_1: Acme Inc <billing@acme.io>\nctm_2: Beta LLC <pay@beta.io>",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/customers")
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_customers_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_customers(), "No customers found.")

    def test_list_customers_limit_clamped(self):
        payload = {"data": [
            {"id": f"ctm_{i}", "name": f"C{i}", "email": f"c{i}@x.io"} for i in range(150)
        ]}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_customers(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(get.call_args.kwargs["params"], {"per_page": 100})

    def test_list_transactions(self):
        payload = {
            "data": [
                {"id": "txn_1", "status": "completed",
                 "details": {"totals": {"grand_total": "1000", "currency_code": "USD"}}},
                {"id": "txn_2", "status": "draft",
                 "details": {"totals": {"grand_total": 2549, "currency_code": "EUR"}}},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_transactions(limit=2)
        self.assertEqual(
            result,
            "txn_1: $10.00 USD [completed]\ntxn_2: $25.49 EUR [draft]",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/transactions")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_transactions_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_transactions(), "No transactions found.")

    def test_list_transactions_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_transactions()
        self.assertIn("ERROR: Paddle API 500 on /transactions.", str(ctx.exception))

    def test_list_transactions_network_error(self):
        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_transactions()
        self.assertIn("ERROR: Paddle API request failed", str(ctx.exception))

    def test_operations_disconnected_raise(self):
        from zeline.connectors import store

        store.delete("paddle")
        for op in (self.conn.list_customers, self.conn.list_transactions):
            with self.assertRaises(RuntimeError) as ctx:
                op()
            self.assertIn("zeline connect paddle", str(ctx.exception))


class PaddleRegistryTests(unittest.TestCase):
    def test_paddle_registered(self):
        from zeline.connectors import get

        conn = get("paddle")
        self.assertIsInstance(conn, PaddleConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(paddle_mod.PaddleConnector.id, "paddle")


if __name__ == "__main__":
    unittest.main()
