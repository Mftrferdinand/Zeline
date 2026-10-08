"""Tests for the SendGrid connector (API key, Bearer auth). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.sendgrid import SendGridConnector

BASE = "https://api.sendgrid.com/v3"
KEY = "SG.SECRET-KEY"


class FakeResponse:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("sendgrid", {"api_key": KEY})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sendgrid-test-"))
        _patch_store(self, self.tmp)
        self.conn = SendGridConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"scopes": ["mail.send"]})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key=KEY)
        self.assertEqual(result, "Connected to SendGrid.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/scopes")
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(store.load("sendgrid"), {"api_key": KEY})

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"errors": [{"message": "bad key"}]}, status=401)
        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            with mock.patch("requests.get", return_value=fake):
                result = self.conn.connect(api_key="SG.BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        self.assertIsNone(store.load("sendgrid"))

    def test_connect_missing_key(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(api_key="  ").startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key=KEY)
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn(KEY, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "SendGrid disconnected.")
        self.assertEqual(self.conn.disconnect(), "SendGrid was not connected.")


class SendEmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sendgrid-test-"))
        _patch_store(self, self.tmp)
        self.conn = SendGridConnector()
        _seed_connected(self.tmp)

    def test_send_email_posts_mail_send(self):
        # SendGrid returns 202 Accepted with an empty body on success.
        with mock.patch("requests.request", return_value=FakeResponse(status=202)) as req:
            result = self.conn.send_email(
                "to@example.com", "hello", "body text", "from@example.com"
            )
        self.assertEqual(result, "Email sent to to@example.com.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE}/mail/send")
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {KEY}"})
        payload = kwargs["json"]
        self.assertEqual(payload["personalizations"], [{"to": [{"email": "to@example.com"}]}])
        self.assertEqual(payload["from"], {"email": "from@example.com"})
        self.assertEqual(payload["subject"], "hello")
        self.assertEqual(payload["content"], [{"type": "text/plain", "value": "body text"}])

    def test_send_email_missing_args(self):
        self.assertTrue(self.conn.send_email("", "s", "b", "f@x").startswith("ERROR:"))
        self.assertTrue(self.conn.send_email("t@x", "", "b", "f@x").startswith("ERROR:"))
        self.assertTrue(self.conn.send_email("t@x", "s", " ", "f@x").startswith("ERROR:"))
        self.assertTrue(self.conn.send_email("t@x", "s", "b", "").startswith("ERROR:"))

    def test_send_email_api_error(self):
        fake = FakeResponse({"errors": [{"message": "bad from"}]}, status=400)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_email("t@x", "s", "b", "f@x")
        self.assertIn("ERROR: SendGrid API 400", str(ctx.exception))

    def test_send_email_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("sendgrid")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_email("t@x", "s", "b", "f@x")
        self.assertIn("zeline connect sendgrid", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
