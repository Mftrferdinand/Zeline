"""Tests for the Pushover connector (user key + app token). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.pushover import PushoverConnector

BASE = "https://api.pushover.net/1"
USER = "uSECRETUSERKEY"
TOKEN = "aSECRETAPPTOKEN"


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


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("pushover", {"user_key": USER, "app_token": TOKEN})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pushover-test-"))
        _patch_store(self, self.tmp)
        self.conn = PushoverConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"status": 1, "request": "req-1"})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.connect(user_key=USER, app_token=TOKEN)
        self.assertEqual(result, "Connected to Pushover.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], f"{BASE}/users/validate.json")
        self.assertEqual(kwargs["data"], {"token": TOKEN, "user": USER})
        saved = store.load("pushover")
        self.assertEqual(saved["user_key"], USER)
        self.assertEqual(saved["app_token"], TOKEN)

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse(
            {"status": 0, "errors": ["application token is invalid"], "request": "req-2"},
            status=200,
        )
        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            with mock.patch("requests.post", return_value=fake):
                result = self.conn.connect(user_key=USER, app_token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        self.assertIsNone(store.load("pushover"))

    def test_connect_http_401_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"status": 0}, status=401)
        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            with mock.patch("requests.post", return_value=fake):
                result = self.conn.connect(user_key=USER, app_token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        self.assertIsNone(store.load("pushover"))

    def test_connect_missing_params(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(user_key=USER).startswith("ERROR:"))
        self.assertTrue(self.conn.connect(app_token=TOKEN).startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(user_key=USER, app_token=TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn(USER, str(status))
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Pushover disconnected.")
        self.assertEqual(self.conn.disconnect(), "Pushover was not connected.")


class SendNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pushover-test-"))
        _patch_store(self, self.tmp)
        self.conn = PushoverConnector()
        _seed_connected(self.tmp)

    def test_send_notification_returns_request_id(self):
        fake = FakeResponse({"status": 1, "request": "req-abc"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.send_notification("hello", title="Hi", priority=1)
        self.assertEqual(result, "sent (request req-abc)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE}/messages.json")
        self.assertEqual(
            kwargs["data"],
            {
                "token": TOKEN,
                "user": USER,
                "message": "hello",
                "title": "Hi",
                "priority": 1,
            },
        )

    def test_send_notification_defaults_omit_title(self):
        fake = FakeResponse({"status": 1, "request": "req-2"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.send_notification("ping")
        self.assertEqual(result, "sent (request req-2)")
        data = req.call_args[1]["data"]
        self.assertNotIn("title", data)
        self.assertEqual(data["priority"], 0)

    def test_send_notification_priority_clamped(self):
        fake = FakeResponse({"status": 1, "request": "req-3"})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.send_notification("x", priority=99)
        self.assertEqual(req.call_args[1]["data"]["priority"], 2)
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.send_notification("x", priority=-99)
        self.assertEqual(req.call_args[1]["data"]["priority"], -2)

    def test_send_notification_empty_message(self):
        self.assertTrue(self.conn.send_notification("").startswith("ERROR:"))
        self.assertTrue(self.conn.send_notification("   ").startswith("ERROR:"))

    def test_send_notification_status_zero_raises(self):
        fake = FakeResponse(
            {"status": 0, "errors": ["user identifier is invalid"], "request": "req-4"}
        )
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_notification("hi")
        self.assertIn("ERROR: Pushover API error", str(ctx.exception))

    def test_send_notification_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({"status": 0}, status=429)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_notification("hi")
        self.assertIn("ERROR: Pushover API 429", str(ctx.exception))

    def test_send_notification_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("pushover")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_notification("hi")
        self.assertIn("zeline connect pushover", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
