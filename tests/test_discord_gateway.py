"""Discord gateway — config validation, registry wiring, message chunking.

No network: every assertion here is about pure logic. ``_send_message`` runs
against a stub ``requests.post`` so the Discord length limit is actually
verified instead of assumed.

unittest, bukan pytest: CI menjalankan ``python -m unittest discover`` dan pytest
tidak terpasang di sana.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from zeline.gateways import GATEWAYS, discord


class _Response:
    def raise_for_status(self):
        return None


class RegistryTests(unittest.TestCase):
    def test_registered_and_labelled(self):
        self.assertIn("discord", GATEWAYS)
        self.assertEqual(discord.info()["label"], "Discord")

    def test_every_registered_gateway_has_config_defaults(self):
        """`zeline gateway enable <name>` builds its config from these defaults.

        A gateway present in the registry but absent from defaults made the
        command raise ValueError on a name its own help text listed as valid.
        """
        from zeline import config

        missing = sorted(set(GATEWAYS) - set(config._defaults()["gateways"]))
        self.assertEqual(missing, [], f"registered gateways without defaults: {missing}")


class ValidationTests(unittest.TestCase):
    def test_bot_token_is_required(self):
        self.assertEqual(discord.validate_config({"token": "abc"}), [])
        self.assertEqual(discord.validate_config({}), ["Discord Bot Token is required"])
        self.assertEqual(discord.validate_config({"token": "   "}),
                         ["Discord Bot Token is required"])

    def test_intents_cover_guild_and_dm_message_content(self):
        """Message content is a privileged intent; without it every message is empty."""
        for bit in (1, 512, 4096, 32768):
            self.assertEqual(discord.INTENTS & bit, bit)

    def test_start_rejects_a_missing_token_before_touching_the_network(self):
        with self.assertRaisesRegex(RuntimeError, "Bot Token"):
            discord.start(None, {}, stop_event=None)


class SendMessageTests(unittest.TestCase):
    def _capture(self):
        sent: list[str] = []

        def fake_post(url, headers=None, json=None, timeout=None):
            sent.append((json or {})["content"])
            return _Response()

        return sent, fake_post

    def test_splits_on_the_discord_length_limit(self):
        sent, fake_post = self._capture()
        body = "x" * (discord.MESSAGE_LIMIT + 10)
        with mock.patch.object(discord.requests, "post", fake_post):
            discord._send_message("token", "chan", body)
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(sent[0]), discord.MESSAGE_LIMIT)
        self.assertEqual(len(sent[1]), 10)
        self.assertEqual("".join(sent), body)
        self.assertLessEqual(discord.MESSAGE_LIMIT, 2000)

    def test_empty_reply_still_posts(self):
        """An empty reply must not silently send nothing — the user sees a bubble."""
        sent, fake_post = self._capture()
        with mock.patch.object(discord.requests, "post", fake_post):
            discord._send_message("token", "chan", "")
        self.assertEqual(sent, [""])


class HeartbeatTests(unittest.TestCase):
    """The keepalive thread belongs to one connection, not to the reconnect loop.

    As a closure over ``start``'s locals it read whatever the loop had rebound
    since. After a reconnect, a thread from the dead connection would wait on the
    new stop event and write to the new socket next to the new thread: two
    writers interleave frames, Discord drops the link, and each reconnect leaks
    another thread until the bot is connected but receives nothing.
    """

    class _Socket:
        def __init__(self):
            self.frames: list[dict] = []

        def send(self, raw):
            self.frames.append(__import__("json").loads(raw))

    def test_a_heartbeat_only_ever_writes_to_its_own_socket(self):
        first, second = self._Socket(), self._Socket()
        first_stop, second_stop = threading.Event(), threading.Event()
        first_state = {"sequence": 7}
        second_state = {"sequence": 99}

        threads = [
            threading.Thread(
                target=discord._heartbeat_loop,
                args=(sock, 0.01, stop, state),
                daemon=True,
            )
            for sock, stop, state in (
                (first, first_stop, first_state),
                (second, second_stop, second_state),
            )
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.08)
        # Retiring the first connection must not silence the second.
        first_stop.set()
        threads[0].join(timeout=2)
        self.assertFalse(threads[0].is_alive())
        sent_after_stop = len(second.frames)
        time.sleep(0.05)
        second_stop.set()
        threads[1].join(timeout=2)
        self.assertFalse(threads[1].is_alive())

        self.assertTrue(first.frames, "the first heartbeat never sent anything")
        self.assertGreater(len(second.frames), sent_after_stop)
        self.assertTrue(all(frame["op"] == 1 for frame in first.frames + second.frames))
        self.assertTrue(all(frame["d"] == 7 for frame in first.frames))
        self.assertTrue(all(frame["d"] == 99 for frame in second.frames))

    def test_the_heartbeat_reports_the_latest_sequence_the_reader_saw(self):
        # Discord treats a stale sequence as a desynchronised session and forces
        # a full reconnect, so the shared state must be read on every beat.
        socket = self._Socket()
        stop = threading.Event()
        state: dict = {"sequence": None}
        thread = threading.Thread(
            target=discord._heartbeat_loop, args=(socket, 0.01, stop, state), daemon=True
        )
        thread.start()
        time.sleep(0.05)
        state["sequence"] = 42
        time.sleep(0.05)
        stop.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(socket.frames[-1]["d"], 42)

    def test_a_dead_socket_retires_the_thread_instead_of_spinning(self):
        class Broken:
            def send(self, raw):
                raise OSError("socket closed")

        stop = threading.Event()
        thread = threading.Thread(
            target=discord._heartbeat_loop,
            args=(Broken(), 0.01, stop, {"sequence": None}),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
