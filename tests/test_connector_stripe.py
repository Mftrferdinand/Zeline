"""Tests for the Stripe connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.stripe import StripeConnector


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


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("stripe", {"secret_key": "sk_secret-test"})


class StripeConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-stripe-test-"))
        _patch_store(self, self.tmp)
        self.conn = StripeConnector()

    def test_connect_empty_key_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            with mock.patch("zeline.connectors.store.save") as save:
                self.assertTrue(self.conn.connect(secret_key=bad).startswith("ERROR:"))
                save.assert_not_called()
        self.assertIsNone(store.load("stripe"))

    def test_connect_bad_key_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": {"message": "Invalid API Key"}}, status=401)
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(secret_key="sk_bad-test")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/v1/account"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk_bad-test")
        self.assertIsNone(store.load("stripe"))

    def test_connect_network_error(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(secret_key="sk_secret-test")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("could not reach", result)
        self.assertIsNone(store.load("stripe"))

    def test_connect_success_saves_key(self):
        fake = FakeResponse({"id": "acct_123", "business_profile": {"name": "Acme Inc"}})
        with mock.patch("requests.get", return_value=fake), mock.patch(
            "zeline.connectors.store.save"
        ) as save:
            result = self.conn.connect(secret_key="sk_secret-test")
        self.assertEqual(result, "Connected to Stripe account Acme Inc.")
        self.assertNotIn("sk_secret-test", result)
        save.assert_called_once_with("stripe", {"secret_key": "sk_secret-test"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_never_leaks_key(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("sk_secret-test", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Stripe disconnected.")
        self.assertEqual(self.conn.disconnect(), "Stripe was not connected.")


class StripeOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-stripe-test-"))
        _patch_store(self, self.tmp)
        self.conn = StripeConnector()
        _seed_connected(self.tmp)

    def test_list_charges(self):
        fake = FakeResponse(
            {
                "data": [
                    {"id": "ch_1", "amount": 2000, "currency": "usd", "status": "succeeded"},
                    {"id": "ch_2", "amount": 500, "currency": "eur", "status": "pending"},
                ]
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_charges(limit=10)
        self.assertEqual(
            result, "ch_1: 2000 usd [succeeded]\nch_2: 500 eur [pending]"
        )
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/v1/charges"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk_secret-test")
        self.assertEqual(kwargs["params"], {"limit": 10})

    def test_list_charges_limit_clamped(self):
        fake = FakeResponse({"data": []})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.list_charges(limit=500)
            self.assertEqual(req.call_args[1]["params"]["limit"], 100)
            self.conn.list_charges(limit=0)
            self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_list_charges_empty(self):
        fake = FakeResponse({"data": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_charges(), "No charges found.")

    def test_list_customers(self):
        fake = FakeResponse(
            {
                "data": [
                    {"id": "cus_1", "email": "jane@example.com", "name": "Jane"},
                    {"id": "cus_2", "email": "bob@example.com", "name": None},
                    {"id": "cus_3", "email": None, "name": None},
                ]
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_customers(limit=10)
        self.assertEqual(
            result,
            "cus_1: jane@example.com (Jane)\ncus_2: bob@example.com\ncus_3: ?",
        )
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/v1/customers"))
        self.assertEqual(kwargs["params"], {"limit": 10})

    def test_list_customers_empty(self):
        fake = FakeResponse({"data": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_customers(), "No customers found.")


class StripeErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-stripe-test-"))
        _patch_store(self, self.tmp)
        self.conn = StripeConnector()
        _seed_connected(self.tmp)

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Stripe API request failed"):
                self.conn.list_charges()

    def test_operation_http_403(self):
        fake = FakeResponse({"error": {"message": "forbidden"}}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Stripe API 403"):
                self.conn.list_charges()
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Stripe API 403"):
                self.conn.list_customers()

    def test_operation_without_connect(self):
        from zeline.connectors import store

        store.delete("stripe")
        with self.assertRaisesRegex(RuntimeError, r"not connected"):
            self.conn.list_charges()


if __name__ == "__main__":
    unittest.main()
