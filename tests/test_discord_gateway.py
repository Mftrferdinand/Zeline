"""Discord gateway — config validation, registry wiring, message chunking.

No network: every assertion here is about pure logic. ``_send_message`` runs
against a stub ``requests.post`` so the Discord length limit is actually
verified instead of assumed.

unittest, bukan pytest: CI menjalankan ``python -m unittest discover`` dan pytest
tidak terpasang di sana.
"""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
