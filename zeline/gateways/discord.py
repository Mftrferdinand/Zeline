"""Discord gateway for Zeline using the official Bot API and Gateway websocket.

User setup requires only a Discord Bot Token. Gateway URL, heartbeat, intents,
and REST endpoints are internal transport details and are never prompted.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

API = "https://discord.com/api/v10"
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
# Guilds + Guild Messages + Direct Messages + Message Content.
INTENTS = 1 | 512 | 4096 | 32768
MESSAGE_LIMIT = 1900


def info() -> dict[str, str]:
    return {"label": "Discord", "hint": "Discord bot via Bot Token."}


def validate_config(cfg: dict[str, Any]) -> list[str]:
    token = str(cfg.get("token", "")).strip()
    return [] if token else ["Discord Bot Token is required"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def _send_message(token: str, channel_id: str, text: str) -> None:
    chunks = [text[i:i + MESSAGE_LIMIT] for i in range(0, len(text), MESSAGE_LIMIT)] or [""]
    for chunk in chunks:
        response = requests.post(
            f"{API}/channels/{channel_id}/messages",
            headers=_headers(token), json={"content": chunk}, timeout=30,
        )
        response.raise_for_status()


def _verify_bot(token: str) -> str:
    response = requests.get(f"{API}/users/@me", headers=_headers(token), timeout=30)
    response.raise_for_status()
    return str(response.json().get("id", ""))


def start(sessions, cfg: dict[str, Any], stop_event, ready=None) -> None:
    errors = validate_config(cfg)
    if errors:
        raise RuntimeError("; ".join(errors))
    try:
        import websocket
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Discord gateway requires the 'websocket-client' package "
            "(pip install websocket-client)."
        ) from exc
    token = str(cfg["token"]).strip()
    bot_id = _verify_bot(token)
    profile = str(cfg.get("tool_profile", "safe"))
    workspace = cfg.get("workspace")
    allowed = {str(value) for value in cfg.get("allowed", []) if str(value)}
    if ready:
        ready.set()

    while not stop_event.is_set():
        ws = None
        heartbeat_stop = threading.Event()
        try:
            ws = websocket.create_connection(GATEWAY, timeout=45)
            hello = json.loads(ws.recv())
            interval = float(hello.get("d", {}).get("heartbeat_interval", 45000)) / 1000
            sequence: int | None = None

            def heartbeat() -> None:
                while not heartbeat_stop.wait(interval):
                    try:
                        if ws is None:
                            return
                        ws.send(json.dumps({"op": 1, "d": sequence}))
                    except Exception:
                        return

            threading.Thread(target=heartbeat, daemon=True).start()
            ws.send(json.dumps({"op": 2, "d": {
                "token": token, "intents": INTENTS,
                "properties": {"os": "linux", "browser": "zeline", "device": "zeline"},
            }}))
            while not stop_event.is_set():
                payload = json.loads(ws.recv())
                if payload.get("s") is not None:
                    sequence = int(payload["s"])
                if payload.get("op") == 7:
                    break
                if payload.get("op") != 0 or payload.get("t") != "MESSAGE_CREATE":
                    continue
                message = payload.get("d") or {}
                author = message.get("author") or {}
                author_id = str(author.get("id", ""))
                if author.get("bot") or author_id == bot_id or (allowed and author_id not in allowed):
                    continue
                content = str(message.get("content", "")).strip()
                channel_id = str(message.get("channel_id", ""))
                if not content or not channel_id:
                    continue
                identity = f"discord:{channel_id}:{author_id}"
                try:
                    reply = sessions.send(identity, content, profile, workspace)
                except Exception as exc:
                    reply = f"Error: {exc}"
                _send_message(token, channel_id, reply)
        except Exception:
            if stop_event.wait(3):
                break
        finally:
            heartbeat_stop.set()
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
