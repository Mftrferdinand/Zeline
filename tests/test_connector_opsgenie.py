"""Tests for the Opsgenie connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import opsgenie as opsgenie_mod
from zeline.connectors.opsgenie import OpsgenieConnector


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

    store.save("opsgenie", {"api_key": "SECRET-API-KEY"})


class OpsgenieConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-opsgenie-test-"))
        _patch_store(self, self.tmp)
        self.conn = OpsgenieConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"data": [], "total_count": 0})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="KEY123")
        self.assertEqual(result, "Connected to Opsgenie.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.opsgenie.com/v2/alerts")
        self.assertEqual(kwargs["headers"]["Authorization"], "GenieKey KEY123")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("opsgenie")
        self.assertEqual(saved["api_key"], "KEY123")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("opsgenie"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertEqual(self.conn.connect(), "ERROR: api_key is required.")
            self.assertEqual(self.conn.connect(api_key="  "), "ERROR: api_key is required.")
        get.assert_not_called()
        self.assertIsNone(store.load("opsgenie"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-API-KEY", status["detail"])
        self.assertNotIn("SECRET-API-KEY", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Opsgenie disconnected.")
        self.assertEqual(self.conn.disconnect(), "Opsgenie was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "opsgenie")
        self.assertEqual(self.conn.name, "Opsgenie")
        self.assertEqual(self.conn.auth_kind, "pat")
        self.assertEqual(self.conn.description, "List alerts in Opsgenie.")


class OpsgenieOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-opsgenie-test-"))
        _patch_store(self, self.tmp)
        self.conn = OpsgenieConnector()
        _seed_connected()

    def test_list_alerts(self):
        payload = {
            "data": [
                {"message": "CPU high", "priority": "P1", "status": "open"},
                {"message": None, "priority": "P5", "status": "closed"},
            ],
            "total_count": 2,
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_alerts(limit=2)
        self.assertEqual(result, "CPU high [P1] (open)\n- [P5] (closed)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.opsgenie.com/v2/alerts")
        self.assertEqual(kwargs["headers"]["Authorization"], "GenieKey SECRET-API-KEY")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_alerts_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_alerts(), "No alerts found.")

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_alerts(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})) as req:
            self.conn.list_alerts(limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_alerts()
        self.assertIn("ERROR: Opsgenie API 403 on /alerts.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_alerts()
        self.assertIn("ERROR: Opsgenie API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("opsgenie")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_alerts()
        self.assertIn("zeline connect opsgenie", str(ctx.exception))

    def test_secret_never_leaks_in_output(self):
        payload = {"data": [{"message": "alert", "priority": "P3", "status": "open"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_alerts()
        self.assertNotIn("SECRET-API-KEY", out)
        self.assertNotIn("SECRET-API-KEY", str(self.conn.status()))


class OpsgenieRegistryTests(unittest.TestCase):
    def test_opsgenie_registered(self):
        from zeline.connectors import get

        conn = get("opsgenie")
        self.assertIsInstance(conn, OpsgenieConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(opsgenie_mod.OpsgenieConnector.id, "opsgenie")


if __name__ == "__main__":
    unittest.main()
