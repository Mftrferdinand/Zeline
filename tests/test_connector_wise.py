"""Tests for the Wise connector. All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import wise as wise_mod
from zeline.connectors.wise import WiseConnector

API_BASE = "https://api.wise.com"


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

    store.save("wise", {"api_token": "TOKEN"})


class WiseConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wise-test-"))
        _patch_store(self, self.tmp)
        self.conn = WiseConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse([{"id": 1}])) as get:
            result = self.conn.connect(api_token="TOKEN")
        self.assertEqual(result, "Connected to Wise.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/v1/profiles")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer TOKEN"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("wise"), {"api_token": "TOKEN"})

    def test_connect_no_token_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(api_token=""), "ERROR: no API token provided.")
        self.assertIsNone(store.load("wise"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_token="TOKEN")
        self.assertTrue(result.startswith("ERROR: could not reach api.wise.com"))
        self.assertIsNone(store.load("wise"))

    def test_connect_http_403_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=403)):
            result = self.conn.connect(api_token="BAD")
        self.assertTrue(result.startswith("ERROR: Wise rejected the API token"))
        self.assertIn("403", result)
        self.assertIsNone(store.load("wise"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API token stored"})
        self.assertNotIn("TOKEN", str(self.conn.status()))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Wise disconnected.")
        self.assertEqual(self.conn.disconnect(), "Wise was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "wise")
        self.assertEqual(self.conn.name, "Wise")
        self.assertEqual(self.conn.auth_kind, "pat")


class WiseOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wise-test-"))
        _patch_store(self, self.tmp)
        self.conn = WiseConnector()
        _seed_connected()

    def test_list_profiles(self):
        payload = [
            {"id": 12345, "type": "personal", "details": {"firstName": "John"}},
            {"id": 67890, "type": "business", "details": {"companyName": "Acme"}},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_profiles()
        self.assertEqual(result, "12345: personal (John)\n67890: business (Acme)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/v1/profiles")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer TOKEN")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_profiles_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_profiles(), "No profiles found.")

    def test_list_profiles_unexpected_body(self):
        with mock.patch("requests.request", return_value=FakeResponse({"oops": True})):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_profiles()
        self.assertIn("ERROR: Wise returned an unexpected profiles response", str(ctx.exception))

    def test_list_profiles_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_profiles()
        self.assertIn("ERROR: Wise API 500 on /v1/profiles.", str(ctx.exception))

    def test_list_profiles_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_profiles()
        self.assertIn("ERROR: Wise API request failed", str(ctx.exception))

    def test_get_rate(self):
        payload = [{"source": "USD", "target": "EUR", "rate": 0.9234}]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.get_rate("USD", "EUR")
        self.assertEqual(result, "USD→EUR: 0.9234")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/v1/rates")
        self.assertEqual(kwargs["params"], {"source": "USD", "target": "EUR"})

    def test_get_rate_default_pair(self):
        with mock.patch("requests.request", return_value=FakeResponse([{"rate": 0.92}])) as req:
            self.assertEqual(self.conn.get_rate(), "USD→EUR: 0.92")
        self.assertEqual(req.call_args.kwargs["params"], {"source": "USD", "target": "EUR"})

    def test_get_rate_no_rates(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_rate("USD", "EUR")
        self.assertIn("ERROR: no rate found for USD to EUR", str(ctx.exception))

    def test_get_rate_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=400)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_rate("USD", "EUR")
        self.assertIn("ERROR: Wise API 400 on /v1/rates.", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("wise")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_profiles()
        self.assertIn("zeline connect wise", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_rate()


class WiseRegistryTests(unittest.TestCase):
    def test_module_import_does_not_leak(self):
        self.assertEqual(wise_mod.WiseConnector.id, "wise")


if __name__ == "__main__":
    unittest.main()
