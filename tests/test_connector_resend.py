"""Tests for the Resend connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import resend as resend_mod
from zeline.connectors.resend import ResendConnector


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

    store.save("resend", {"api_key": "SECRET-KEY"})


class ResendConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-resend-test-"))
        _patch_store(self, self.tmp)
        self.conn = ResendConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"data": []})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="KEY")
        self.assertEqual(result, "Connected to Resend.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.resend.com/domains")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer KEY")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("resend")
        self.assertEqual(saved["api_key"], "KEY")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Invalid API key"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("resend"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("resend"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-KEY", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Resend disconnected.")
        self.assertEqual(self.conn.disconnect(), "Resend was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "resend")
        self.assertEqual(self.conn.name, "Resend")
        self.assertEqual(self.conn.auth_kind, "pat")


class ResendOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-resend-test-"))
        _patch_store(self, self.tmp)
        self.conn = ResendConnector()
        _seed_connected()

    def test_send_email_single_recipient(self):
        payload = {"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.send_email("a@example.com", "b@example.com", "Hi", "<p>hello</p>")
        self.assertEqual(result, "Email sent: 49a3999c-0ce1-4ea6-ab68-afcd6dc2e794")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.resend.com/emails")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-KEY")
        self.assertEqual(
            kwargs["json"],
            {
                "from": "a@example.com",
                "to": ["b@example.com"],
                "subject": "Hi",
                "html": "<p>hello</p>",
            },
        )

    def test_send_email_multiple_recipients(self):
        payload = {"id": "abc123"}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.send_email(
                "a@example.com", ["b@example.com", "c@example.com"], "Hi", "<p>hello</p>"
            )
        self.assertEqual(result, "Email sent: abc123")
        self.assertEqual(req.call_args[1]["json"]["to"], ["b@example.com", "c@example.com"])

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=422)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_email("a@example.com", "b@example.com", "Hi", "<p>x</p>")
        self.assertIn("ERROR: Resend API 422 on /emails.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_email("a@example.com", "b@example.com", "Hi", "<p>x</p>")
        self.assertIn("ERROR: Resend API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("resend")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_email("a@example.com", "b@example.com", "Hi", "<p>x</p>")
        self.assertIn("zeline connect resend", str(ctx.exception))

    def test_secret_never_leaks_in_output(self):
        payload = {"id": "abc123"}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.send_email("a@example.com", "b@example.com", "Hi", "<p>x</p>")
        self.assertNotIn("SECRET-KEY", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-KEY", str(status))


class ResendRegistryTests(unittest.TestCase):
    def test_resend_registered(self):
        from zeline.connectors import get

        conn = get("resend")
        self.assertIsInstance(conn, ResendConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(resend_mod.ResendConnector.id, "resend")


if __name__ == "__main__":
    unittest.main()
