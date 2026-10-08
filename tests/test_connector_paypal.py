"""Tests for the PayPal connector. All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from requests.auth import HTTPBasicAuth

from zeline.connectors import paypal as paypal_mod
from zeline.connectors.paypal import PayPalConnector

API_BASE = "https://api-m.paypal.com"


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

    store.save("paypal", {"client_id": "CID", "access_token": "ATOKEN"})


class PayPalConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pp-test-"))
        _patch_store(self, self.tmp)
        self.conn = PayPalConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        payload = {"access_token": "ATOKEN", "token_type": "Bearer", "expires_in": 3600}
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            result = self.conn.connect(client_id="CID", client_secret="CSECRET")
        self.assertEqual(result, "Connected to PayPal.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], f"{API_BASE}/v1/oauth2/token")
        self.assertEqual(kwargs["data"], {"grant_type": "client_credentials"})
        self.assertEqual(kwargs["timeout"], 30)
        auth = kwargs["auth"]
        self.assertIsInstance(auth, HTTPBasicAuth)
        self.assertEqual(auth.username, "CID")
        self.assertEqual(auth.password, "CSECRET")
        self.assertEqual(store.load("paypal"), {"client_id": "CID", "access_token": "ATOKEN"})

    def test_connect_missing_credentials_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(
            self.conn.connect(client_id="", client_secret=""),
            "ERROR: client_id and client_secret are both required.",
        )
        self.assertIsNone(store.load("paypal"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(client_id="CID", client_secret="CSECRET")
        self.assertTrue(result.startswith("ERROR: could not reach api-m.paypal.com"))
        self.assertIsNone(store.load("paypal"))

    def test_connect_http_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(client_id="BAD", client_secret="BAD")
        self.assertTrue(result.startswith("ERROR: PayPal rejected the credentials"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("paypal"))

    def test_connect_no_access_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({})):
            result = self.conn.connect(client_id="CID", client_secret="CSECRET")
        self.assertTrue(result.startswith("ERROR: PayPal did not return an access token"))
        self.assertIsNone(store.load("paypal"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "linked (token cached)"})
        self.assertNotIn("ATOKEN", str(self.conn.status()))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "PayPal disconnected.")
        self.assertEqual(self.conn.disconnect(), "PayPal was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "paypal")
        self.assertEqual(self.conn.name, "PayPal")
        self.assertEqual(self.conn.auth_kind, "pat")


class PayPalOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pp-test-"))
        _patch_store(self, self.tmp)
        self.conn = PayPalConnector()
        _seed_connected()

    def test_list_invoices(self):
        payload = {
            "items": [
                {
                    "id": "INV-001",
                    "status": "SENT",
                    "amount": {"currency_code": "USD", "value": "100.00"},
                },
                {
                    "id": "INV-002",
                    "status": "DRAFT",
                    "amount": {"currency_code": "EUR", "value": "50.00"},
                },
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_invoices(limit=10)
        self.assertEqual(result, "INV-001: $100.00 USD (SENT)\nINV-002: $50.00 EUR (DRAFT)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/v2/invoicing/invoices")
        self.assertEqual(kwargs["params"], {"page_size": 10})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ATOKEN")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_invoices_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})):
            self.assertEqual(self.conn.list_invoices(), "No invoices found.")

    def test_list_invoices_limit_clamped(self):
        items = [
            {
                "id": f"INV-{i:03d}",
                "status": "SENT",
                "amount": {"currency_code": "USD", "value": "1.00"},
            }
            for i in range(120)
        ]
        with mock.patch("requests.request", return_value=FakeResponse({"items": items})) as req:
            result = self.conn.list_invoices(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"page_size": 100})

    def test_list_invoices_401_suggests_reconnect(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_invoices()
        self.assertIn("401", str(ctx.exception))
        self.assertIn("zeline connect paypal", str(ctx.exception))

    def test_list_invoices_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_invoices()
        self.assertIn("ERROR: PayPal API 500 on /v2/invoicing/invoices.", str(ctx.exception))

    def test_list_invoices_network_error(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_invoices()
        self.assertIn("ERROR: PayPal API request failed", str(ctx.exception))

    def test_get_order(self):
        payload = {
            "id": "ORD-123",
            "status": "APPROVED",
            "purchase_units": [
                {"amount": {"currency_code": "USD", "value": "250.00"}},
            ],
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.get_order("ORD-123")
        self.assertEqual(result, "Order ORD-123: APPROVED, 250.00 USD")
        self.assertEqual(req.call_args.args[1], f"{API_BASE}/v2/checkout/orders/ORD-123")

    def test_get_order_no_id(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.get_order("  ")
        self.assertEqual(str(ctx.exception), "ERROR: no order id provided.")

    def test_get_order_404(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_order("MISSING")
        self.assertIn("ERROR: PayPal API 404 on /v2/checkout/orders/MISSING.", str(ctx.exception))

    def test_auth_headers_missing_store_raises(self):
        from zeline.connectors import store

        store.delete("paypal")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn._auth_headers()
        self.assertIn("zeline connect paypal", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("paypal")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_invoices()
        self.assertIn("zeline connect paypal", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_order("ORD-123")


class PayPalRegistryTests(unittest.TestCase):
    def test_module_import_does_not_leak(self):
        self.assertEqual(paypal_mod.PayPalConnector.id, "paypal")

    def test_docstring_notes_sandbox(self):
        self.assertIn("api-m.sandbox.paypal.com", paypal_mod.__doc__)


if __name__ == "__main__":
    unittest.main()
