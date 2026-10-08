"""Slack gateway for Zeline using the official Slack Web API.

User setup requires a Slack Bot Token (xoxb-...). Uses Socket Mode for
receiving messages (no public URL needed) or Events API webhook.
This implementation uses the Web API for sending and Socket Mode for receiving.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

API = "https://slack.com/api"
MESSAGE_LIMIT = 3900  # Slack mrkdwn limit is 4000


def info() -> dict[str, str]:
    return {"label": "Slack", "hint": "Slack bot via Bot Token (xoxb-...)."}


def validate_config(cfg: dict[str, Any]) -> list[str]:
    token = (cfg.get("token") or "").strip()
    if not token:
        return ["Slack Bot Token is required (xoxb-...)"]
    if not token.startswith("xoxb-"):
        return ["Token should start with xoxb- (Bot Token)"]
    return []


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _send_message(token: str, channel: str, text: str) -> None:
    chunks = [text[i:i + MESSAGE_LIMIT] for i in range(0, len(text), MESSAGE_LIMIT)] or [""]
    for chunk in chunks:
        resp = requests.post(
            f"{API}/chat.postMessage",
            headers=_headers(token),
            json={"channel": channel, "text": chunk, "mrkdwn": True},
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack send failed: {data.get('error')}")


def _send_with_buttons(token: str, channel: str, text: str,
                       options: list[str]) -> None:
    """Send message with interactive buttons (Block Kit)."""
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:2990]}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": opt[:75]},
                    "value": opt,
                    "action_id": f"zeline_opt_{i}",
                }
                for i, opt in enumerate(options[:5])
            ],
        },
    ]
    resp = requests.post(
        f"{API}/chat.postMessage",
        headers=_headers(token),
        json={"channel": channel, "blocks": blocks, "text": text[:150]},
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack buttons failed: {data.get('error')}")


class SlackGateway:
    """Long-polling Slack gateway using Socket Mode."""

    def __init__(self, token: str, app_token: str, on_message):
        self.token = token
        self.app_token = app_token  # xapp-... for Socket Mode
        self.on_message = on_message
        self._stop = threading.Event()
        self._ws = None

    def _get_socket_url(self) -> str:
        resp = requests.post(
            f"{API}/apps.connections.open",
            headers={"Authorization": f"Bearer {self.app_token}"},
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Socket Mode failed: {data.get('error')}")
        return data["url"]

    def run(self) -> None:
        try:
            import websocket
        except ImportError:
            raise RuntimeError("websocket-client required: pip install websocket-client")
        url = self._get_socket_url()
        self._ws = websocket.create_connection(url, timeout=60)
        while not self._stop.is_set():
            try:
                msg = self._ws.recv()
                if not msg:
                    continue
                data = json.loads(msg)
                self._handle_socket(data)
            except Exception:
                if not self._stop.is_set():
                    time.sleep(5)
                    try:
                        url = self._get_socket_url()
                        self._ws = websocket.create_connection(url, timeout=60)
                    except Exception:
                        pass

    def _handle_socket(self, data: dict) -> None:
        # Acknowledge envelope
        if "envelope_id" in data:
            self._ws.send(json.dumps({"envelope_id": data["envelope_id"]}))
        payload = data.get("payload", {})
        event = payload.get("event", {})
        if event.get("type") == "message" and not event.get("bot_id"):
            self.on_message({
                "channel": event.get("channel"),
                "user": event.get("user"),
                "text": event.get("text", ""),
                "ts": event.get("ts"),
            })

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass


def run_gateway(config: dict[str, Any], on_message) -> None:
    """Entry point for the gateway runner."""
    token = config["token"]
    app_token = config.get("app_token", "")
    if not app_token:
        raise RuntimeError("Slack Socket Mode requires app_token (xapp-...)")
    gw = SlackGateway(token, app_token, on_message)
    gw.run()


def start(sessions, cfg: dict[str, Any], stop_event) -> None:
    """Gateway interface: start(sessions, cfg, stop_event)."""
    def on_message(identity: str, text: str, chat_id: str) -> str:
        return f"[Slack] Message received: {text[:100]}"
    run_gateway(cfg, on_message)
