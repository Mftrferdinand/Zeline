"""Tests for the Mailgun connector (API key + domain). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import mailgun as mailgun_mod
from zeline.connectors.mailgun import MailgunConnector


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

    store.save("mailgun", {"api_key": "SECRET-KEY", "domain": "mg.example.com"})


class MailgunConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-mailgun-test-"))
        _patch_store(self, self.tmp)
        self.conn = MailgunConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"name": "mg.example.com"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="KEY", domain="mg.example.com")
        self.assertEqual(result, "Connected to Mailgun (domain mg.example.com).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.mailgun.net/v3/mg.example.com")
        self.assertEqual(kwargs["auth"], ("api", "KEY"))
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("mailgun")
        self.assertEqual(saved["api_key"], "KEY")
        self.assertEqual(saved["domain"], "mg.example.com")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD", domain="mg.example.com")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("mailgun"))

    def test_connect_empty_credentials_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="KEY").startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="  ", domain="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("mailgun"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY", domain="mg.example.com")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "domain mg.example.com")
        self.assertNotIn("SECRET-KEY", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Mailgun disconnected.")
        self.assertEqual(self.conn.disconnect(), "Mailgun was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "mailgun")
        self.assertEqual(self.conn.name, "Mailgun")
        self.assertEqual(self.conn.auth_kind, "pat")


class MailgunOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-mailgun-test-"))
        _patch_store(self, self.tmp)
        self.conn = MailgunConnector()
        _seed_connected()

    def test_send_email(self):
        payload = {"id": "<20261008@mailgun.org>", "message": "Queued."}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.send_email("a@example.com", "b@example.com", "Hi", "hello")
        self.assertEqual(result, "Email queued: <20261008@mailgun.org>")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.mailgun.net/v3/mg.example.com/messages")
        self.assertEqual(kwargs["auth"], ("api", "SECRET-KEY"))
        self.assertEqual(
            kwargs["data"],
            {"from": "a@example.com", "to": "b@example.com", "subject": "Hi", "text": "hello"},
        )

    def test_list_messages(self):
        payload = {
            "items": [
                {"timestamp": 1788829200, "event": "delivered", "recipient": "b@example.com"},
                {"timestamp": 1788829100, "event": "accepted", "recipient": "c@example.com"},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_messages(limit=2)
        self.assertEqual(
            result,
            "1788829200 delivered — b@example.com\n1788829100 accepted — c@example.com",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.mailgun.net/v3/mg.example.com/events")
        self.assertEqual(kwargs["auth"], ("api", "SECRET-KEY"))
        self.assertEqual(kwargs["params"], {"limit": 2})

    def test_list_messages_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})):
            self.assertEqual(self.conn.list_messages(), "No messages found.")

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})) as req:
            self.conn.list_messages(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse({"items": []})) as req:
            self.conn.list_messages(limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_messages()
        self.assertIn("ERROR: Mailgun API 403 on /events.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_email("a@example.com", "b@example.com", "Hi", "hello")
        self.assertIn("ERROR: Mailgun API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("mailgun")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_messages()
        self.assertIn("zeline connect mailgun", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.send_email("a@example.com", "b@example.com", "Hi", "hello")

    def test_secret_never_leaks_in_output(self):
        payload = {"items": [{"timestamp": 1, "event": "delivered", "recipient": "b@example.com"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_messages()
        self.assertNotIn("SECRET-KEY", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-KEY", str(status))


class MailgunRegistryTests(unittest.TestCase):
    def test_mailgun_registered(self):
        from zeline.connectors import get

        conn = get("mailgun")
        self.assertIsInstance(conn, MailgunConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(mailgun_mod.MailgunConnector.id, "mailgun")


if __name__ == "__main__":
    unittest.main()
