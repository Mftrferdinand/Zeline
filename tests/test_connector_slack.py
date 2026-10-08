"""Tests for the Slack connector. All HTTP is mocked; no real network or tokens."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.slack import SlackConnector


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


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save(
        "slack",
        {"token": "xoxb-secret-token", "team": "Acme", "user": "zeline-bot"},
    )


class SlackConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-slack-test-"))
        _patch_store(self, self.tmp)
        self.conn = SlackConnector()

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            with mock.patch("zeline.connectors.store.save") as save:
                self.assertTrue(self.conn.connect(token=bad).startswith("ERROR:"))
                save.assert_not_called()
        self.assertIsNone(store.load("slack"))

    def test_connect_auth_test_not_ok_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"ok": False, "error": "invalid_auth"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="xoxb-bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("invalid_auth", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/auth.test"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer xoxb-bad")
        self.assertIsNone(store.load("slack"))

    def test_connect_http_401_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"ok": False}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="xoxb-bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("slack"))

    def test_connect_network_error(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="xoxb-abc")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("could not reach", result)
        self.assertIsNone(store.load("slack"))

    def test_connect_success_saves_token_team_user(self):
        from zeline.connectors import store

        fake = FakeResponse({"ok": True, "team": "Acme", "user": "zeline-bot"})
        with mock.patch("requests.get", return_value=fake), mock.patch(
            "zeline.connectors.store.save"
        ) as save:
            result = self.conn.connect(token="xoxb-secret-token")
        self.assertIn("Connected to Slack workspace Acme as zeline-bot.", result)
        self.assertNotIn("xoxb-secret-token", result)
        save.assert_called_once_with(
            "slack",
            {"token": "xoxb-secret-token", "team": "Acme", "user": "zeline-bot"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_never_leaks_token(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("xoxb-secret-token", status["detail"])
        self.assertNotIn("secret", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Slack disconnected.")
        self.assertEqual(self.conn.disconnect(), "Slack was not connected.")


class SlackOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-slack-test-"))
        _patch_store(self, self.tmp)
        self.conn = SlackConnector()
        _seed_connected(self.tmp)

    def test_list_channels(self):
        fake = FakeResponse(
            {
                "ok": True,
                "channels": [
                    {"id": "C111", "name": "general"},
                    {"id": "C222", "name": "random"},
                ],
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_channels(limit=10)
        self.assertEqual(result, "#general (C111)\n#random (C222)")
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/conversations.list"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer xoxb-secret-token")
        params = kwargs["params"]
        self.assertEqual(params["limit"], 10)
        self.assertEqual(params["types"], "public_channel,private_channel")

    def test_list_channels_limit_clamped(self):
        fake = FakeResponse({"ok": True, "channels": []})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.list_channels(limit=500)
            self.assertEqual(req.call_args[1]["params"]["limit"], 100)
            self.conn.list_channels(limit=0)
            self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_list_channels_empty(self):
        fake = FakeResponse({"ok": True, "channels": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_channels(), "No channels found.")

    def test_send_message(self):
        fake = FakeResponse({"ok": True, "ts": "1718000000.0001"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.send_message("C111", "hello world")
        self.assertEqual(result, "Message sent to C111 (ts 1718000000.0001).")
        self.assertNotIn("xoxb-secret-token", result)
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/chat.postMessage"))
        self.assertEqual(kwargs["json"], {"channel": "C111", "text": "hello world"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer xoxb-secret-token")

    def test_read_history(self):
        fake = FakeResponse(
            {
                "ok": True,
                "messages": [
                    {"user": "U1", "text": "hello there"},
                    {"user": "U2", "text": "line one\nline two"},
                ],
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.read_history("C111", limit=5)
        self.assertEqual(result, "U1: hello there\nU2: line one line two")
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/conversations.history"))
        self.assertEqual(kwargs["params"], {"channel": "C111", "limit": 5})

    def test_read_history_empty(self):
        fake = FakeResponse({"ok": True, "messages": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.read_history("C111"), "No messages in C111.")


class SlackErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-slack-test-"))
        _patch_store(self, self.tmp)
        self.conn = SlackConnector()
        _seed_connected(self.tmp)

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Slack API request failed"):
                self.conn.list_channels()

    def test_operation_http_403(self):
        fake = FakeResponse({"ok": False}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Slack API 403"):
                self.conn.read_history("C111")

    def test_operation_ok_false_raises_slack_error(self):
        fake = FakeResponse({"ok": False, "error": "channel_not_found"})
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(
                RuntimeError, r"^ERROR: Slack API error: channel_not_found"
            ):
                self.conn.list_channels()
            with self.assertRaisesRegex(
                RuntimeError, r"^ERROR: Slack API error: channel_not_found"
            ):
                self.conn.send_message("C999", "hi")
            with self.assertRaisesRegex(
                RuntimeError, r"^ERROR: Slack API error: channel_not_found"
            ):
                self.conn.read_history("C999")


if __name__ == "__main__":
    unittest.main()
