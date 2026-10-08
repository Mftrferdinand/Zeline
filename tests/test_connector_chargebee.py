"""Tests for the Chargebee connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import chargebee as chargebee_mod
from zeline.connectors.chargebee import ChargebeeConnector

API_BASE = "https://acme.chargebee.com/api/v2"


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

    store.save("chargebee", {"api_key": "cb_key-test", "site": "acme"})


class ChargebeeConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cb-test-"))
        _patch_store(self, self.tmp)
        self.conn = ChargebeeConnector()

    def test_connect_success_saves_creds(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"list": []})) as get:
            result = self.conn.connect(api_key="cb_key-test", site="acme")
        self.assertEqual(result, "Connected to Chargebee site 'acme'.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/customers")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("chargebee"), {"api_key": "cb_key-test", "site": "acme"}
        )

    def test_connect_missing_params_errors(self):
        from zeline.connectors import store

        for bad in ({"api_key": "", "site": "acme"}, {"api_key": "k", "site": ""}, {"api_key": "  "}):
            result = self.conn.connect(**bad)
            self.assertTrue(result.startswith("ERROR:"))
            self.assertIn("api_key and site", result)
        self.assertIsNone(store.load("chargebee"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="k", site="acme")
        self.assertTrue(result.startswith("ERROR: could not reach Chargebee"))
        self.assertIsNone(store.load("chargebee"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_key="bad", site="acme")
        self.assertTrue(result.startswith("ERROR: Chargebee rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("chargebee"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "site: acme"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Chargebee disconnected.")
        self.assertEqual(self.conn.disconnect(), "Chargebee was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "chargebee")
        self.assertEqual(self.conn.name, "Chargebee")
        self.assertEqual(self.conn.auth_kind, "pat")


class ChargebeeOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cb-test-"))
        _patch_store(self, self.tmp)
        self.conn = ChargebeeConnector()
        _seed_connected()

    def test_list_customers(self):
        payload = {
            "list": [
                {"customer": {"id": "cus_1", "first_name": "Ada", "last_name": "Lovelace",
                              "email": "ada@example.com"}},
                {"customer": {"id": "cus_2", "first_name": "Alan", "last_name": "Turing",
                              "email": "alan@example.com"}},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_customers(limit=2)
        self.assertEqual(
            result,
            "cus_1: Ada Lovelace <ada@example.com>\ncus_2: Alan Turing <alan@example.com>",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/customers")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_customers_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"list": []})):
            self.assertEqual(self.conn.list_customers(), "No customers found.")

    def test_list_customers_limit_clamped(self):
        payload = {"list": [
            {"customer": {"id": f"cus_{i}", "first_name": "N", "last_name": "M",
                          "email": "n@m.io"}} for i in range(150)
        ]}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_customers(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 100})

    def test_list_subscriptions(self):
        payload = {
            "list": [
                {"subscription": {"id": "sub_1", "plan_id": "pro-monthly",
                                  "plan_amount": 4999, "status": "active"}},
                {"subscription": {"id": "sub_2", "plan_id": "starter",
                                  "plan_amount": 999, "status": "cancelled"}},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_subscriptions(limit=2)
        self.assertEqual(
            result,
            "sub_1: pro-monthly ($49.99) [active]\nsub_2: starter ($9.99) [cancelled]",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/subscriptions")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_subscriptions_item_prices_fallback(self):
        payload = {
            "list": [
                {"subscription": {"id": "sub_3", "status": "in_trial",
                                  "subscription_items": [
                                      {"item_price_id": "plan-a", "unit_price": 1200},
                                      {"item_price_id": "plan-b", "unit_price": 800},
                                  ]}},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.list_subscriptions()
        self.assertEqual(result, "sub_3: plan-a, plan-b ($20.00) [in_trial]")

    def test_list_subscriptions_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"list": []})):
            self.assertEqual(self.conn.list_subscriptions(), "No subscriptions found.")

    def test_list_customers_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_customers()
        self.assertIn("ERROR: Chargebee API 403 on /customers.", str(ctx.exception))

    def test_list_customers_network_error(self):
        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_customers()
        self.assertIn("ERROR: Chargebee API request failed", str(ctx.exception))

    def test_operations_disconnected_raise(self):
        from zeline.connectors import store

        store.delete("chargebee")
        for op in (self.conn.list_customers, self.conn.list_subscriptions):
            with self.assertRaises(RuntimeError) as ctx:
                op()
            self.assertIn("zeline connect chargebee", str(ctx.exception))


class ChargebeeRegistryTests(unittest.TestCase):
    def test_chargebee_registered(self):
        from zeline.connectors import get

        conn = get("chargebee")
        self.assertIsInstance(conn, ChargebeeConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(chargebee_mod.ChargebeeConnector.id, "chargebee")


if __name__ == "__main__":
    unittest.main()
