"""Tests for the Vonage connector (API key + secret). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import vonage as vonage_mod
from zeline.connectors.vonage import VonageConnector


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

    store.save("vonage", {"api_key": "SECRET-KEY", "api_secret": "SECRET-SECRET"})


class VonageConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-vonage-test-"))
        _patch_store(self, self.tmp)
        self.conn = VonageConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"value": 12.34, "autoReload": False})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="KEY", api_secret="SECRET")
        self.assertEqual(result, "Connected to Vonage.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://rest.nexmo.com/account/get-balance")
        self.assertEqual(kwargs["params"], {"api_key": "KEY", "api_secret": "SECRET"})
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("vonage")
        self.assertEqual(saved["api_key"], "KEY")
        self.assertEqual(saved["api_secret"], "SECRET")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error-code": "401", "error-code-label": "authentication failed"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD", api_secret="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("vonage"))

    def test_connect_empty_credentials_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="KEY").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_secret="SECRET").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="  ", api_secret="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("vonage"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY", api_secret="SECRET")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-KEY", status["detail"])
        self.assertNotIn("SECRET-SECRET", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Vonage disconnected.")
        self.assertEqual(self.conn.disconnect(), "Vonage was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "vonage")
        self.assertEqual(self.conn.name, "Vonage")
        self.assertEqual(self.conn.description, "Send SMS via Vonage.")
        self.assertEqual(self.conn.auth_kind, "pat")


class VonageOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-vonage-test-"))
        _patch_store(self, self.tmp)
        self.conn = VonageConnector()
        _seed_connected()

    def test_send_sms_success(self):
        payload = {"messages": [{"status": "0", "message-id": "abc123"}]}
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            result = self.conn.send_sms(to="15551234567", from_name="Zeline", text="Hello")
        self.assertEqual(result, "SMS sent to 15551234567.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://rest.nexmo.com/sms/json")
        self.assertEqual(kwargs["timeout"], 30)
        sent = kwargs["json"]
        self.assertEqual(sent["api_key"], "SECRET-KEY")
        self.assertEqual(sent["api_secret"], "SECRET-SECRET")
        self.assertEqual(sent["to"], "15551234567")
        self.assertEqual(sent["from"], "Zeline")
        self.assertEqual(sent["text"], "Hello")

    def test_send_sms_provider_failure(self):
        payload = {"messages": [{"status": "4", "error-text": "invalid credentials"}]}
        with mock.patch("requests.post", return_value=FakeResponse(payload)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_sms(to="15551234567", from_name="Zeline", text="Hello")
        self.assertIn("ERROR: Vonage SMS failed (invalid credentials).", str(ctx.exception))

    def test_send_sms_http_error(self):
        with mock.patch("requests.post", return_value=FakeResponse({}, status=429)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_sms(to="15551234567", from_name="Zeline", text="Hello")
        self.assertIn("ERROR: Vonage API 429 on /sms/json.", str(ctx.exception))

    def test_send_sms_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_sms(to="15551234567", from_name="Zeline", text="Hello")
        self.assertIn("ERROR: Vonage API request failed", str(ctx.exception))

    def test_send_sms_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("vonage")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_sms(to="15551234567", from_name="Zeline", text="Hello")
        self.assertIn("zeline connect vonage", str(ctx.exception))

    def test_secret_never_leaks_in_output(self):
        payload = {"messages": [{"status": "0", "message-id": "abc123"}]}
        with mock.patch("requests.post", return_value=FakeResponse(payload)):
            out = self.conn.send_sms(to="15551234567", from_name="Zeline", text="Hello")
        self.assertNotIn("SECRET-KEY", out)
        self.assertNotIn("SECRET-SECRET", out)
        self.assertNotIn("SECRET-KEY", str(self.conn.status()))
        self.assertNotIn("SECRET-SECRET", str(self.conn.status()))


class VonageRegistryTests(unittest.TestCase):
    def test_vonage_registered(self):
        from zeline.connectors import get

        conn = get("vonage")
        self.assertIsInstance(conn, VonageConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(vonage_mod.VonageConnector.id, "vonage")


if __name__ == "__main__":
    unittest.main()
