"""Discord gateway — config validation, registry wiring, message chunking.

No network: every assertion here is about pure logic. `_send_message` is
exercised with a stub `requests.post` so the 2000-char Discord limit is actually
verified instead of assumed.
"""
from __future__ import annotations

import pytest

from zeline.gateways import GATEWAYS, discord


def test_registered_and_labelled():
    assert "discord" in GATEWAYS
    assert discord.info()["label"] == "Discord"


def test_every_registered_gateway_has_config_defaults():
    """`zeline gateway enable <name>` builds its config from these defaults.

    A gateway present in the registry but absent from defaults made the command
    raise ValueError on a name its own help text listed as valid.
    """
    from zeline import config

    defaults = config._defaults()["gateways"]
    missing = sorted(set(GATEWAYS) - set(defaults))
    assert not missing, f"registered gateways without config defaults: {missing}"


def test_validate_config_requires_a_bot_token():
    assert discord.validate_config({"token": "abc"}) == []
    assert discord.validate_config({}) == ["Discord Bot Token is required"]
    assert discord.validate_config({"token": "   "}) == ["Discord Bot Token is required"]


def test_intents_cover_guild_and_dm_message_content():
    """Message content is a privileged intent; without it every message is empty."""
    guilds, guild_messages, direct_messages, message_content = 1, 512, 4096, 32768
    for bit in (guilds, guild_messages, direct_messages, message_content):
        assert discord.INTENTS & bit == bit


def test_send_message_splits_on_the_discord_length_limit(monkeypatch):
    sent: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append((json or {})["content"])
        return Response()

    monkeypatch.setattr(discord.requests, "post", fake_post)
    discord._send_message("token", "chan", "x" * (discord.MESSAGE_LIMIT + 10))
    assert len(sent) == 2
    assert len(sent[0]) == discord.MESSAGE_LIMIT
    assert len(sent[1]) == 10
    assert "".join(sent) == "x" * (discord.MESSAGE_LIMIT + 10)
    assert discord.MESSAGE_LIMIT <= 2000


def test_send_message_still_posts_for_empty_text(monkeypatch):
    """An empty reply must not silently send nothing — the user sees a bubble."""
    sent: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append((json or {})["content"])
        return Response()

    monkeypatch.setattr(discord.requests, "post", fake_post)
    discord._send_message("token", "chan", "")
    assert sent == [""]


def test_start_rejects_a_missing_token_before_touching_the_network():
    with pytest.raises(RuntimeError, match="Bot Token"):
        discord.start(None, {}, stop_event=None)
