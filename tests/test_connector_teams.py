"""Tests for the Teams connector (incoming-webhook URL). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.teams import TeamsConnector

GOOD_URL = "https://outlook.office.com/webhook/abc/IncomingWebhook/xyz/123"
WORKFLOW_URL = "https://prod-11.westus.webhook.office.com/workflows/def"


class FakeResponse:
    def __init__(self, payload=None, status=200, text="1"):
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

    store.save("teams", {"url": GOOD_URL})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-teams-test-"))
        _patch_store(self, self.tmp)
        self.conn = TeamsConnector()

    def test_connect_success_saves_webhook(self):
        from zeline.connectors import store

        # Webhook-only service: connect performs no HTTP call.
        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            result = self.conn.connect(url=GOOD_URL)
        self.assertEqual(result, "Connected to Microsoft Teams.")
        save.assert_called_once_with("teams", {"url": GOOD_URL})
        self.assertEqual(store.load("teams"), {"url": GOOD_URL})

    def test_connect_accepts_workflow_host(self):
        with mock.patch("zeline.connectors.store.save"):
            result = self.conn.connect(url=WORKFLOW_URL)
        self.assertEqual(result, "Connected to Microsoft Teams.")

    def test_connect_bad_token_stores_nothing(self):
        # A non-Teams URL (or empty string) is rejected without any store write.
        from zeline.connectors import store

        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            result = self.conn.connect(url="https://example.com/hook/123")
        self.assertEqual(result, "ERROR: not a Teams webhook URL")
        save.assert_not_called()
        self.assertIsNone(store.load("teams"))

    def test_connect_missing_url(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(url="   ").startswith("ERROR:"))

    def test_connect_rejects_bad_urls(self):
        from zeline.connectors import store

        for bad in (
            "http://outlook.office.com/webhook/x",
            "https://evil-outlook.office.com.evil.com/hook",
            "https://webhook.office.com/x",  # bare apex host is not the workflow host
            "outlook.office.com/webhook/x",
            "https://webhook.office.com.evil.com/x",
        ):
            with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
                result = self.conn.connect(url=bad)
            self.assertEqual(result, "ERROR: not a Teams webhook URL", bad)
            save.assert_not_called()

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        # The webhook URL is a secret: it must never appear in status.
        self.assertNotIn("outlook.office.com", str(status))
        self.assertNotIn("IncomingWebhook", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Microsoft Teams disconnected.")
        self.assertEqual(self.conn.disconnect(), "Microsoft Teams was not connected.")


class SendMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-teams-test-"))
        _patch_store(self, self.tmp)
        self.conn = TeamsConnector()
        _seed_connected(self.tmp)

    def test_send_message_posts_to_webhook(self):
        with mock.patch("requests.request", return_value=FakeResponse()) as req:
            result = self.conn.send_message("hello team")
        self.assertEqual(result, "Message sent to Teams.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], GOOD_URL)
        self.assertEqual(kwargs["json"], {"text": "hello team"})

    def test_send_message_empty_text(self):
        self.assertTrue(self.conn.send_message("").startswith("ERROR:"))
        self.assertTrue(self.conn.send_message("   ").startswith("ERROR:"))

    def test_send_message_api_error(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse(status=403, text="forbidden")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_message("hi")
        self.assertIn("ERROR: Teams API 403", str(ctx.exception))

    def test_send_message_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_message("hi")
        self.assertTrue(str(ctx.exception).startswith("ERROR: Teams API request failed"))

    def test_send_message_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("teams")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_message("hi")
        self.assertIn("zeline connect teams", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
