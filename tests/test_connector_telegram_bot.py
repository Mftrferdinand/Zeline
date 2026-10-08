"""Tests for the Telegram Bot connector (Bot API). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import store
from zeline.connectors.telegram_bot import TelegramBotConnector

_TOKEN = "TEST:token-value"
_BAD_TOKEN = "BAD:token-value"


class FakeResponse:
    def __init__(self, payload=None, status=200, broken_json=False):
        self._payload = payload
        self.status_code = status
        self._broken_json = broken_json
        self.text = str(payload)

    def json(self):
        if self._broken_json:
            raise ValueError("no JSON")
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    store.save("telegram_bot", {"token": _TOKEN, "username": "testbot"})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tgbot-test-"))
        _patch_store(self, self.tmp)
        self.conn = TelegramBotConnector()

    def _mock_get(self, testcase, resp=None, side_effect=None):
        patcher = mock.patch("requests.get")
        mocked = patcher.start()
        testcase.addCleanup(patcher.stop)
        if side_effect is not None:
            mocked.side_effect = side_effect
        else:
            mocked.return_value = resp
        return mocked

    def test_connect_empty_token_returns_error_and_saves_nothing(self):
        out = self.conn.connect(token="")
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIsNone(store.load("telegram_bot"))

    def test_connect_whitespace_token_returns_error(self):
        out = self.conn.connect(token="   ")
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIsNone(store.load("telegram_bot"))

    def test_connect_401_returns_error_and_saves_nothing(self):
        self._mock_get(self, FakeResponse(status=401))
        out = self.conn.connect(token=_BAD_TOKEN)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertNotIn(_BAD_TOKEN, out)
        self.assertIsNone(store.load("telegram_bot"))

    def test_connect_ok_false_returns_error_and_saves_nothing(self):
        self._mock_get(
            self,
            FakeResponse(
                {"ok": False, "error_code": 401, "description": "Unauthorized"},
                status=200,
            ),
        )
        out = self.conn.connect(token=_BAD_TOKEN)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertNotIn(_BAD_TOKEN, out)
        self.assertIsNone(store.load("telegram_bot"))

    def test_connect_network_failure_returns_error_and_saves_nothing(self):
        self._mock_get(
            self, side_effect=requests.RequestException("connection refused")
        )
        out = self.conn.connect(token=_TOKEN)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertNotIn(_TOKEN, out)
        self.assertIsNone(store.load("telegram_bot"))

    def test_connect_success_saves_token_and_username(self):
        self._mock_get(
            self,
            FakeResponse(
                {"ok": True, "result": {"id": 123, "username": "testbot"}}
            ),
        )
        out = self.conn.connect(token=_TOKEN)
        self.assertNotIn(_TOKEN, out)
        self.assertIn("@testbot", out)
        saved = store.load("telegram_bot")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["token"], _TOKEN)
        self.assertEqual(saved["username"], "testbot")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tgbot-test-"))
        _patch_store(self, self.tmp)
        self.conn = TelegramBotConnector()

    def test_status_not_connected(self):
        status = self.conn.status()
        self.assertFalse(status["connected"])
        self.assertEqual(status["detail"], "not linked")
        self.assertNotIn(_TOKEN, str(status))

    def test_status_connected_shows_username_without_token(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertIn("testbot", status["detail"])
        self.assertNotIn(_TOKEN, status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect_when_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Telegram Bot disconnected.")
        self.assertFalse(self.conn.is_connected())

    def test_disconnect_when_not_connected(self):
        self.assertEqual(self.conn.disconnect(), "Telegram Bot was not connected.")


class GetMeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tgbot-test-"))
        _patch_store(self, self.tmp)
        self.conn = TelegramBotConnector()
        self.patcher = mock.patch("requests.post")
        self.post = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_get_me_returns_bot_info(self):
        _seed_connected()
        self.post.return_value = FakeResponse(
            {"ok": True, "result": {"id": 123, "username": "testbot"}}
        )
        out = self.conn.get_me()
        self.assertIn("@testbot", out)
        self.assertIn("123", out)
        self.assertNotIn(_TOKEN, out)

    def test_get_me_when_not_connected_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.get_me()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_get_me_telegram_error_raises(self):
        _seed_connected()
        self.post.return_value = FakeResponse(
            {"ok": False, "error_code": 401, "description": "Unauthorized"},
            status=401,
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.get_me()
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("ERROR:"))
        self.assertIn("Unauthorized", msg)
        self.assertNotIn(_TOKEN, msg)


class SendMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tgbot-test-"))
        _patch_store(self, self.tmp)
        self.conn = TelegramBotConnector()
        self.patcher = mock.patch("requests.post")
        self.post = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_send_message_success(self):
        _seed_connected()
        self.post.return_value = FakeResponse(
            {"ok": True, "result": {"message_id": 42}}
        )
        out = self.conn.send_message(chat_id=999, text="hello")
        self.assertEqual(out, "Pesan terkirim (message_id 42).")
        args, kwargs = self.post.call_args
        self.assertEqual(kwargs["json"], {"chat_id": 999, "text": "hello"})
        self.assertNotIn(_TOKEN, str(kwargs.get("json")))

    def test_send_message_telegram_error_raises(self):
        _seed_connected()
        self.post.return_value = FakeResponse(
            {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
            status=400,
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_message(chat_id=1, text="x")
        msg = str(ctx.exception)
        self.assertEqual(msg, "ERROR: Telegram API: Bad Request: chat not found.")
        self.assertNotIn(_TOKEN, msg)

    def test_send_message_unreadable_body_raises(self):
        _seed_connected()
        self.post.return_value = FakeResponse(broken_json=True, status=502)
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_message(chat_id=1, text="x")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("ERROR:"))
        self.assertNotIn(_TOKEN, msg)

    def test_send_message_network_failure_raises(self):
        _seed_connected()
        self.post.side_effect = requests.RequestException("timeout")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_message(chat_id=1, text="x")
        msg = str(ctx.exception)
        self.assertTrue(msg.startswith("ERROR:"))
        self.assertNotIn(_TOKEN, msg)

    def test_send_message_when_not_connected_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_message(chat_id=1, text="x")
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))


class SecretHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tgbot-test-"))
        _patch_store(self, self.tmp)
        self.conn = TelegramBotConnector()

    def test_token_never_leaks_in_error_strings(self):
        # Failure paths that mention "token" must not include its value.
        with mock.patch("requests.get") as get:
            get.return_value = FakeResponse(status=401)
            out = self.conn.connect(token=_TOKEN)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertNotIn(_TOKEN, out)


if __name__ == "__main__":
    unittest.main()
