"""Tests for the Discord connector (bot token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import discord as discord_mod
from zeline.connectors.discord import DiscordConnector


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

    store.save("discord", {"token": "BOT-SECRET", "user": "MyBot"})


class DiscordConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-discord-test-"))
        _patch_store(self, self.tmp)
        self.conn = DiscordConnector()

    def test_connect_empty_token(self):
        for result in (self.conn.connect(), self.conn.connect(token="  ")):
            self.assertTrue(result.startswith("ERROR:"), result)

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "123", "username": "MyBot", "bot": True})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="BOT-SECRET")
        self.assertIn("Connected to Discord as @MyBot.", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/users/@me"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot BOT-SECRET")
        saved = store.load("discord")
        self.assertEqual(saved["token"], "BOT-SECRET")
        self.assertEqual(saved["user"], "MyBot")

    def test_connect_success_bot_field_absent_still_ok(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "123", "username": "MyBot"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BOT-SECRET")
        self.assertIn("Connected to Discord as @MyBot.", result)
        self.assertIsNotNone(store.load("discord"))

    def test_connect_non_bot_token_rejected(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "123", "username": "human", "bot": False})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="USER-TOKEN")
        self.assertTrue(result.startswith("ERROR:"), result)
        self.assertIsNone(store.load("discord"))

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "401: Unauthorized", "code": 0}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"), result)
        self.assertIsNone(store.load("discord"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="BOT-SECRET")
        self.assertTrue(result.startswith("ERROR: could not reach"), result)

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertIn("MyBot", status["detail"])
        self.assertNotIn("BOT-SECRET", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Discord disconnected.")
        self.assertEqual(self.conn.disconnect(), "Discord was not connected.")


class DiscordOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-discord-test-"))
        _patch_store(self, self.tmp)
        self.conn = DiscordConnector()
        _seed_connected()

    def test_list_channels(self):
        fake = FakeResponse(
            [
                {"id": "1", "name": "general", "type": 0},
                {"id": "2", "name": "lounge", "type": 2},
                {"id": "3", "name": "news", "type": 5},
            ]
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_channels("GUILD1")
        self.assertIn("#general (text)", result)
        self.assertIn("#lounge (voice)", result)
        self.assertIn("#news (news)", result)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/guilds/GUILD1/channels"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot BOT-SECRET")

    def test_list_channels_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            result = self.conn.list_channels("GUILD1")
        self.assertIn("No channels", result)

    def test_list_channels_missing_guild_id(self):
        with mock.patch("requests.request") as req:
            result = self.conn.list_channels("  ")
        self.assertTrue(result.startswith("ERROR:"), result)
        req.assert_not_called()

    def test_list_channels_api_error_message(self):
        fake = FakeResponse({"message": "Missing Access", "code": 50001}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_channels("GUILD1")
        self.assertEqual(str(ctx.exception), "ERROR: Discord API: Missing Access.")

    def test_list_channels_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_channels("GUILD1")
        self.assertIn("ERROR: Discord API request failed", str(ctx.exception))

    def test_send_message(self):
        fake = FakeResponse({"id": "MSG123", "content": "hello"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.send_message("CHAN1", "hello")
        self.assertEqual(result, "Pesan terkirim: MSG123.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/channels/CHAN1/messages"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot BOT-SECRET")
        self.assertEqual(kwargs["json"], {"content": "hello"})

    def test_send_message_missing_args(self):
        with mock.patch("requests.request") as req:
            self.assertTrue(self.conn.send_message("  ", "hi").startswith("ERROR:"))
            self.assertTrue(self.conn.send_message("CHAN1", "   ").startswith("ERROR:"))
        req.assert_not_called()

    def test_send_message_api_error_message(self):
        fake = FakeResponse(
            {"message": "Cannot send an empty message", "code": 50006}, status=400
        )
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_message("CHAN1", "hi")
        self.assertEqual(str(ctx.exception), "ERROR: Discord API: Cannot send an empty message.")

    def test_operation_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("discord")
        with mock.patch("requests.request") as req:
            for action in (
                lambda: self.conn.list_channels("GUILD1"),
                lambda: self.conn.send_message("CHAN1", "hi"),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    action()
                self.assertIn("zeline connect discord", str(ctx.exception))
        req.assert_not_called()


class SecretLeakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-discord-test-"))
        _patch_store(self, self.tmp)
        self.conn = DiscordConnector()

    def test_token_never_leaks_in_status_or_errors(self):
        _seed_connected()
        status = self.conn.status()
        self.assertNotIn("BOT-SECRET", status["detail"])
        self.assertNotIn("BOT-SECRET", self.conn.connect(token=""))
        fake = FakeResponse({"message": "bad", "code": 0}, status=401)
        with mock.patch("requests.get", return_value=fake):
            self.assertNotIn("BAD", self.conn.connect(token="BAD"))
        with mock.patch(
            "requests.request", return_value=FakeResponse({"message": "nope"}, status=403)
        ):
            try:
                self.conn.list_channels("GUILD1")
            except RuntimeError as exc:
                self.assertNotIn("BOT-SECRET", str(exc))
            try:
                self.conn.send_message("CHAN1", "hi")
            except RuntimeError as exc:
                self.assertNotIn("BOT-SECRET", str(exc))

    def test_saved_file_holds_token_but_status_masks_it(self):
        fake = FakeResponse({"id": "1", "username": "MyBot", "bot": True})
        with mock.patch("requests.get", return_value=fake):
            self.conn.connect(token="BOT-SECRET")
        from zeline.connectors import store

        saved = store.load("discord")
        self.assertEqual(saved["token"], "BOT-SECRET")
        self.assertNotIn("BOT-SECRET", str(self.conn.status()))
        self.assertNotIn("BOT-SECRET", self.conn.disconnect())


class RegistryTests(unittest.TestCase):
    def test_discord_registered(self):
        from zeline.connectors import get

        conn = get("discord")
        self.assertIsInstance(conn, DiscordConnector)
        self.assertEqual(conn.auth_kind, "pat")
        self.assertEqual(conn.id, "discord")
        self.assertEqual(conn.name, "Discord")


if __name__ == "__main__":
    unittest.main()
