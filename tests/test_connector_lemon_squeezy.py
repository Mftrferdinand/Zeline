"""Tests for the Lemon Squeezy connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import lemon_squeezy as lemon_squeezy_mod
from zeline.connectors.lemon_squeezy import LemonSqueezyConnector


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


def _me_payload(name="Aes", email="aes@example.com"):
    return {
        "data": {
            "type": "users",
            "id": "1",
            "attributes": {"name": name, "email": email},
        }
    }


def _seed_connected():
    from zeline.connectors import store

    store.save(
        "lemon_squeezy",
        {
            "api_key": "SECRET-API-KEY",
            "name": "Aes",
            "email": "aes@example.com",
        },
    )


class LemonSqueezyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-lemon-squeezy-test-"))
        _patch_store(self, self.tmp)
        self.conn = LemonSqueezyConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse(_me_payload())
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="API-KEY")
        self.assertEqual(result, "Connected to Lemon Squeezy as Aes.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.lemonsqueezy.com/v1/users/me")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer API-KEY")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("lemon_squeezy")
        self.assertEqual(saved["api_key"], "API-KEY")
        self.assertEqual(saved["name"], "Aes")
        self.assertEqual(saved["email"], "aes@example.com")

    def test_connect_accepts_generic_token_kwarg(self):
        fake = FakeResponse(_me_payload())
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="  GENERIC-KEY  ")
        self.assertTrue(result.startswith("Connected to Lemon Squeezy"))
        self.assertEqual(get.call_args[1]["headers"]["Authorization"], "Bearer GENERIC-KEY")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"errors": [{"title": "Unauthorized"}]}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("lemon_squeezy"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("lemon_squeezy"))

    def test_connect_network_error(self):
        import requests

        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="API-KEY")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("lemon_squeezy"))

    def test_connect_unreadable_response(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad json")
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="API-KEY")
        self.assertTrue(result.startswith("ERROR:"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "connected as Aes")
        self.assertNotIn("SECRET-API-KEY", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Lemon Squeezy disconnected.")
        self.assertEqual(self.conn.disconnect(), "Lemon Squeezy was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "lemon_squeezy")
        self.assertEqual(self.conn.name, "Lemon Squeezy")
        self.assertEqual(
            self.conn.description, "List Lemon Squeezy customers and orders."
        )
        self.assertEqual(self.conn.auth_kind, "pat")


class LemonSqueezyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-lemon-squeezy-test-"))
        _patch_store(self, self.tmp)
        self.conn = LemonSqueezyConnector()
        _seed_connected()

    def _customer_payload(self):
        return {
            "data": [
                {
                    "type": "customers",
                    "id": "1",
                    "attributes": {
                        "name": "John Doe",
                        "email": "john@example.com",
                        "status": "active",
                    },
                },
                {
                    "type": "customers",
                    "id": "2",
                    "attributes": {
                        "name": "Jane Roe",
                        "email": "jane@example.com",
                        "status": "archived",
                    },
                },
            ]
        }

    def _order_payload(self):
        return {
            "data": [
                {
                    "type": "orders",
                    "id": "1",
                    "attributes": {
                        "order_number": 1234,
                        "total_formatted": "$49.00",
                        "status": "paid",
                    },
                },
                {
                    "type": "orders",
                    "id": "2",
                    "attributes": {
                        "order_number": 1235,
                        "total_formatted": "$99.00",
                        "status": "refunded",
                    },
                },
            ]
        }

    def test_list_customers(self):
        with mock.patch("requests.get", return_value=FakeResponse(self._customer_payload())) as get:
            result = self.conn.list_customers(limit=5)
        self.assertEqual(
            result,
            "John Doe <john@example.com> (active)\nJane Roe <jane@example.com> (archived)",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.lemonsqueezy.com/v1/customers")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-API-KEY")
        self.assertEqual(kwargs["params"], {"page[size]": 5})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_customers_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_customers(), "No customers found.")

    def test_list_orders(self):
        with mock.patch("requests.get", return_value=FakeResponse(self._order_payload())) as get:
            result = self.conn.list_orders(limit=5)
        self.assertEqual(
            result,
            "#1234: $49.00 (paid)\n#1235: $99.00 (refunded)",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.lemonsqueezy.com/v1/orders")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-API-KEY")
        self.assertEqual(kwargs["params"], {"page[size]": 5})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_orders_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_orders(), "No orders found.")

    def test_limit_clamped(self):
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})) as get:
            self.conn.list_customers(limit=500)
        self.assertEqual(get.call_args[1]["params"], {"page[size]": 100})
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})) as get:
            self.conn.list_customers(limit=0)
        self.assertEqual(get.call_args[1]["params"], {"page[size]": 1})
        with mock.patch("requests.get", return_value=FakeResponse({"data": []})) as get:
            self.conn.list_orders(limit=999)
        self.assertEqual(get.call_args[1]["params"], {"page[size]": 100})

    def test_api_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_customers()
        self.assertIn("ERROR: Lemon Squeezy API 403 on /customers.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_orders()
        self.assertIn("ERROR: Lemon Squeezy API request failed", str(ctx.exception))

    def test_api_unreadable_response(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad json")
        with mock.patch("requests.get", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_customers()
        self.assertIn("ERROR: Lemon Squeezy returned an unreadable response.", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("lemon_squeezy")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_customers()
        self.assertIn("zeline connect lemon_squeezy", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_orders()

    def test_secret_never_leaks_in_output(self):
        with mock.patch("requests.get", return_value=FakeResponse(self._customer_payload())):
            out = self.conn.list_customers()
        self.assertNotIn("SECRET-API-KEY", out)
        with mock.patch("requests.get", return_value=FakeResponse(self._order_payload())):
            out = self.conn.list_orders()
        self.assertNotIn("SECRET-API-KEY", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-API-KEY", str(status))


class LemonSqueezyRegistryTests(unittest.TestCase):
    def test_lemon_squeezy_registered(self):
        from zeline.connectors import get

        conn = get("lemon_squeezy")
        self.assertIsInstance(conn, LemonSqueezyConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(lemon_squeezy_mod.LemonSqueezyConnector.id, "lemon_squeezy")


if __name__ == "__main__":
    unittest.main()
