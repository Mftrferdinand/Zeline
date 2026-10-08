"""Tests for the Polar connector. All HTTP is mocked; no real network."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import polar as polar_mod
from zeline.connectors.polar import PolarConnector


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

    store.save("polar", {"access_token": "sekret-token-abc123"})


class PolarConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-polar-test-"))
        _patch_store(self, self.tmp)
        self.conn = PolarConnector()

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect(access_token="").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(access_token="   ").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="").startswith("ERROR:"))
        self.assertIsNone(store.load("polar"))

    def test_connect_accepts_generic_token_kwarg(self):
        from zeline.connectors import store

        fake = FakeResponse({"items": []})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="generic-token")
        self.assertEqual(result, "Connected to Polar.")
        self.assertEqual(store.load("polar")["access_token"], "generic-token")

    def test_connect_401_errors_and_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "invalid_token"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(access_token="bad-token")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("polar"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(access_token="tok")
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("polar"))

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        fake = FakeResponse({"items": [{"id": "prod_1", "name": "Widget"}]})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(access_token="good-token")
        self.assertEqual(result, "Connected to Polar.")
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/products"))
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer good-token")
        saved = store.load("polar")
        self.assertEqual(saved["access_token"], "good-token")
        self.assertEqual(len(saved), 1)  # nothing but the token stored

    def test_status_not_linked(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_linked_never_leaks_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "linked")
        self.assertNotIn("sekret-token-abc123", repr(status))
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Polar disconnected.")
        self.assertEqual(self.conn.disconnect(), "Polar was not connected.")


class PolarOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-polar-test-"))
        _patch_store(self, self.tmp)
        self.conn = PolarConnector()
        _seed_connected()

    def test_list_products_formats_items(self):
        payload = {
            "items": [
                {
                    "id": "prod_1",
                    "name": "Pro Plan",
                    "prices": [
                        {
                            "price_amount": 900,
                            "price_currency": "usd",
                            "recurring_interval": "month",
                        }
                    ],
                },
                {"id": "prod_2", "name": "Lifetime", "prices": []},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_products(limit=10)
        self.assertEqual(
            result,
            "prod_1: Pro Plan (900 usd/month)\nprod_2: Lifetime (no price)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/products"))
        self.assertEqual(kwargs["params"], {"limit": 10})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sekret-token-abc123")

    def test_list_products_clamps_limit(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})) as req:
            self.conn.list_products(limit=500)
            self.assertEqual(req.call_args[1]["params"], {"limit": 100})
            self.conn.list_products(limit=0)
            self.assertEqual(req.call_args[1]["params"], {"limit": 1})

    def test_list_products_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})):
            self.assertEqual(self.conn.list_products(), "No products found.")

    def test_list_orders_formats_items(self):
        payload = {
            "items": [
                {
                    "id": "ord_1",
                    "amount": 900,
                    "status": "paid",
                    "created_at": "2026-09-01T10:00:00Z",
                },
                {
                    "id": "ord_2",
                    "amount": 4500,
                    "status": "pending",
                    "created_at": "2026-10-01T08:30:00Z",
                },
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_orders(limit=10)
        self.assertEqual(
            result,
            "ord_1: 900 (status: paid, created: 2026-09-01T10:00:00Z)\n"
            "ord_2: 4500 (status: pending, created: 2026-10-01T08:30:00Z)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/orders"))
        self.assertEqual(kwargs["params"], {"limit": 10})

    def test_list_orders_clamps_limit(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})) as req:
            self.conn.list_orders(limit=999)
            self.assertEqual(req.call_args[1]["params"], {"limit": 100})
            self.conn.list_orders(limit=-5)
            self.assertEqual(req.call_args[1]["params"], {"limit": 1})

    def test_list_orders_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})):
            self.assertEqual(self.conn.list_orders(), "No orders found.")

    def test_api_http_error_raises(self):
        with mock.patch("requests.request", return_value=FakeResponse({"error": "x"}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_products()
        self.assertTrue(str(ctx.exception).startswith("ERROR: Polar API 403"))

    def test_api_network_error_raises(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_orders()
        self.assertIn("request failed", str(ctx.exception))

    def test_operation_without_connection_raises(self):
        from zeline.connectors import store

        store.delete("polar")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_products()
        self.assertTrue(str(ctx.exception).startswith("ERROR: Polar is not connected"))
        with self.assertRaises(RuntimeError):
            self.conn.list_orders()


class PolarSecretHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-polar-test-"))
        _patch_store(self, self.tmp)
        self.conn = PolarConnector()

    def test_error_messages_never_contain_token(self):
        fake = FakeResponse({"error": "bad"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(access_token="my-super-secret")
        self.assertNotIn("my-super-secret", result)
        _seed_connected()
        with mock.patch("requests.request", return_value=FakeResponse({"error": "x"}, status=500)):
            try:
                self.conn.list_orders()
            except RuntimeError as exc:
                self.assertNotIn("sekret-token-abc123", str(exc))

    def test_registration(self):
        from zeline.connectors import get

        conn = get("polar")
        self.assertIsInstance(conn, PolarConnector)
        self.assertEqual(conn.auth_kind, "pat")
        self.assertEqual(conn.name, "Polar")


if __name__ == "__main__":
    unittest.main()
