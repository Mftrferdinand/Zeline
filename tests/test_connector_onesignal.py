"""Tests for the OneSignal connector (app id + REST API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import onesignal as onesignal_mod
from zeline.connectors.onesignal import OneSignalConnector


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

    store.save("onesignal", {"app_id": "SECRET-APP", "api_key": "SECRET-KEY"})


class OneSignalConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-onesignal-test-"))
        _patch_store(self, self.tmp)
        self.conn = OneSignalConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "app-123", "name": "My app"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(app_id="APP-ID", api_key="KEY")
        self.assertEqual(result, "Connected to OneSignal.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.onesignal.com/apps/APP-ID")
        self.assertEqual(kwargs["headers"], {"Authorization": "Basic KEY"})
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("onesignal")
        self.assertEqual(saved["app_id"], "APP-ID")
        self.assertEqual(saved["api_key"], "KEY")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"errors": ["Invalid API key"]}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(app_id="APP-ID", api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("onesignal"))

    def test_connect_empty_credentials_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(app_id="APP-ID").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="KEY").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(app_id="  ", api_key="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("onesignal"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(app_id="APP-ID", api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-APP", status["detail"])
        self.assertNotIn("SECRET-KEY", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "OneSignal disconnected.")
        self.assertEqual(self.conn.disconnect(), "OneSignal was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "onesignal")
        self.assertEqual(self.conn.name, "OneSignal")
        self.assertEqual(self.conn.description, "Send push notifications via OneSignal.")
        self.assertEqual(self.conn.auth_kind, "pat")


class OneSignalOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-onesignal-test-"))
        _patch_store(self, self.tmp)
        self.conn = OneSignalConnector()
        _seed_connected()

    def test_send_push_default_segments(self):
        payload = {"id": "notif-123", "recipients": 100}
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            result = self.conn.send_push(title="Hi", message="Hello")
        self.assertEqual(result, "Push sent: notif-123")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.onesignal.com/notifications")
        self.assertEqual(kwargs["headers"], {"Authorization": "Basic SECRET-KEY"})
        self.assertEqual(kwargs["timeout"], 30)
        sent = kwargs["json"]
        self.assertEqual(sent["app_id"], "SECRET-APP")
        self.assertEqual(sent["included_segments"], ["All"])
        self.assertEqual(sent["headings"], {"en": "Hi"})
        self.assertEqual(sent["contents"], {"en": "Hello"})

    def test_send_push_custom_segments(self):
        payload = {"id": "notif-456"}
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            result = self.conn.send_push(title="Hi", message="Hello", segments=["Active Users"])
        self.assertEqual(result, "Push sent: notif-456")
        self.assertEqual(post.call_args[1]["json"]["included_segments"], ["Active Users"])

    def test_send_push_http_error(self):
        with mock.patch("requests.post", return_value=FakeResponse({"errors": ["bad"]}, status=400)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_push(title="Hi", message="Hello")
        self.assertIn("ERROR: OneSignal API 400 on /notifications.", str(ctx.exception))

    def test_send_push_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_push(title="Hi", message="Hello")
        self.assertIn("ERROR: OneSignal API request failed", str(ctx.exception))

    def test_send_push_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("onesignal")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_push(title="Hi", message="Hello")
        self.assertIn("zeline connect onesignal", str(ctx.exception))

    def test_secret_never_leaks_in_output(self):
        payload = {"id": "notif-123"}
        with mock.patch("requests.post", return_value=FakeResponse(payload)):
            out = self.conn.send_push(title="Hi", message="Hello")
        self.assertNotIn("SECRET-APP", out)
        self.assertNotIn("SECRET-KEY", out)
        self.assertNotIn("SECRET-APP", str(self.conn.status()))
        self.assertNotIn("SECRET-KEY", str(self.conn.status()))


class OneSignalRegistryTests(unittest.TestCase):
    def test_onesignal_registered(self):
        from zeline.connectors import get

        conn = get("onesignal")
        self.assertIsInstance(conn, OneSignalConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(onesignal_mod.OneSignalConnector.id, "onesignal")


if __name__ == "__main__":
    unittest.main()
