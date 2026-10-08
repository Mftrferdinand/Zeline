"""Tests for the Datadog connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.datadog import DatadogConnector


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("datadog", {"api_key": "SECRET-API-KEY", "app_key": "SECRET-APP-KEY"})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-datadog-test-"))
        _patch_store(self, self.tmp)
        self.conn = DatadogConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"valid": True})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="SECRET-API-KEY", app_key="SECRET-APP-KEY")
        self.assertEqual(result, "Connected to Datadog.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.datadoghq.com/api/v1/validate")
        self.assertEqual(kwargs["headers"]["DD-API-KEY"], "SECRET-API-KEY")
        self.assertEqual(kwargs["headers"]["DD-APPLICATION-KEY"], "SECRET-APP-KEY")
        saved = store.load("datadog")
        self.assertEqual(saved["api_key"], "SECRET-API-KEY")
        self.assertEqual(saved["app_key"], "SECRET-APP-KEY")

    def test_connect_invalid_keys_store_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"valid": False}, status=200)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD", app_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("datadog"))

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=403)):
            result = self.conn.connect(api_key="BAD", app_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("datadog"))

    def test_connect_missing_keys(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(api_key="K").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(app_key="K").startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="K", app_key="K")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-API-KEY", str(status))
        self.assertNotIn("SECRET-APP-KEY", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Datadog disconnected.")
        self.assertEqual(self.conn.disconnect(), "Datadog was not connected.")


class ListMonitorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-datadog-test-"))
        _patch_store(self, self.tmp)
        self.conn = DatadogConnector()
        _seed_connected(self.tmp)

    def _monitors(self, n=2):
        return [
            {"id": i, "name": f"monitor {i}", "overall_state": "OK"}
            for i in range(1, n + 1)
        ]

    def test_list_monitors_formats(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._monitors())) as req:
            result = self.conn.list_monitors()
        self.assertEqual(result, "monitor 1 [OK]\nmonitor 2 [OK]")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.datadoghq.com/api/v1/monitor")
        self.assertEqual(kwargs["headers"]["DD-API-KEY"], "SECRET-API-KEY")
        self.assertEqual(kwargs["headers"]["DD-APPLICATION-KEY"], "SECRET-APP-KEY")
        self.assertEqual(kwargs["params"]["limit"], 10)

    def test_list_monitors_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._monitors())) as req:
            self.conn.list_monitors(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse(self._monitors())) as req:
            self.conn.list_monitors(limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_list_monitors_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_monitors(), "No monitors found.")

    def test_list_monitors_api_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertIn("ERROR: Datadog API 401", str(ctx.exception))

    def test_list_monitors_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_monitors()
        self.assertTrue(str(ctx.exception).startswith("ERROR: Datadog API request failed"))

    def test_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("datadog")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_monitors()
        self.assertIn("zeline connect datadog", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
