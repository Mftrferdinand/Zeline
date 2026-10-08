"""Tests for the Coinbase connector. All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import coinbase as coinbase_mod
from zeline.connectors.coinbase import CoinbaseConnector

API_BASE = "https://api.coinbase.com/v2"


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

    store.save("coinbase", {"api_key": "KEY", "user": "aes"})


class CoinbaseConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cb-test-"))
        _patch_store(self, self.tmp)
        self.conn = CoinbaseConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        payload = {"data": {"name": "aes", "email": "a@example.com"}}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.connect(api_key="KEY")
        self.assertEqual(result, "Connected to Coinbase as aes.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/user")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer KEY"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("coinbase"), {"api_key": "KEY", "user": "aes"})

    def test_connect_no_key_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(api_key=""), "ERROR: no API key provided.")
        self.assertIsNone(store.load("coinbase"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach api.coinbase.com"))
        self.assertIsNone(store.load("coinbase"))

    def test_connect_http_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR: Coinbase rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("coinbase"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "aes"})
        self.assertNotIn("KEY", str(self.conn.status()))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Coinbase disconnected.")
        self.assertEqual(self.conn.disconnect(), "Coinbase was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "coinbase")
        self.assertEqual(self.conn.name, "Coinbase")
        self.assertEqual(self.conn.auth_kind, "pat")


class CoinbaseOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cb-test-"))
        _patch_store(self, self.tmp)
        self.conn = CoinbaseConnector()
        _seed_connected()

    def test_list_accounts(self):
        payload = {
            "data": [
                {"name": "BTC Wallet", "balance": {"amount": "0.05", "currency": "BTC"}},
                {"name": "USD Wallet", "balance": {"amount": "120.00", "currency": "USD"}},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_accounts(limit=10)
        self.assertEqual(result, "BTC Wallet: 0.05 BTC\nUSD Wallet: 120.00 USD")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/accounts")
        self.assertEqual(kwargs["params"], {"limit": 10})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer KEY")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_accounts_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_accounts(), "No accounts found.")

    def test_list_accounts_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_accounts()
        self.assertIn("ERROR: Coinbase API 403 on /accounts.", str(ctx.exception))

    def test_list_accounts_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_accounts()
        self.assertIn("ERROR: Coinbase API request failed", str(ctx.exception))

    def test_spot_price(self):
        payload = {"data": {"base": "BTC", "currency": "USD", "amount": "67123.45"}}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.spot_price("BTC-USD")
        self.assertEqual(result, "BTC-USD spot: $67,123.45")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/prices/BTC-USD/spot")

    def test_spot_price_default_pair(self):
        payload = {"data": {"base": "BTC", "currency": "USD", "amount": "67123.45"}}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            self.conn.spot_price()
        self.assertEqual(req.call_args.args[1], f"{API_BASE}/prices/BTC-USD/spot")

    def test_spot_price_normalizes_case(self):
        payload = {"data": {"base": "ETH", "currency": "USD", "amount": "3200.10"}}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.spot_price("eth-usd")
        self.assertEqual(result, "ETH-USD spot: $3,200.10")
        self.assertEqual(req.call_args.args[1], f"{API_BASE}/prices/ETH-USD/spot")

    def test_spot_price_non_numeric_amount(self):
        payload = {"data": {"base": "BTC", "currency": "USD", "amount": "n/a"}}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.spot_price(), "BTC-USD spot: $n/a")

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("coinbase")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_accounts()
        self.assertIn("zeline connect coinbase", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.spot_price()


class CoinbaseRegistryTests(unittest.TestCase):
    def test_module_import_does_not_leak(self):
        self.assertEqual(coinbase_mod.CoinbaseConnector.id, "coinbase")


if __name__ == "__main__":
    unittest.main()
