"""Gateway Telegram Aesora via raw Bot API long polling.

Tidak memakai SDK Telegram agar instalasi tetap kecil dan mudah di Termux.
Setiap chat mendapat identity ``telegram:<chat_id>`` sendiri sehingga history
+ memory tidak pernah tercampur dengan user lain.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from aesora import config
from aesora.agent import AesoraError

API_TEMPLATE = "https://api.telegram.org/bot{token}"
OFFSET_FILE = config.STATE_DIR / "telegram-offset.json"
TELEGRAM_MESSAGE_LIMIT = 4_000


def info() -> dict[str, str]:
    return {
        "label": "Telegram",
        "hint": "Buat bot melalui @BotFather, lalu tempel token Bot API.",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    token = str(cfg.get("token", "")).strip()
    if not token:
        return ["token Telegram kosong"]
    if ":" not in token:
        return ["format token Telegram terlihat tidak valid"]
    profile = str(cfg.get("tool_profile", "safe"))
    if profile not in {"safe", "workspace", "full"}:
        return [f"tool_profile Telegram tidak valid: {profile}"]
    allowed = cfg.get("allowed", [])
    if not isinstance(allowed, list):
        return ["allowed Telegram harus berupa list chat ID"]
    return []


def _api_call(api: str, method: str, *, timeout: int = 65, **params: Any) -> dict[str, Any] | None:
    """Panggil Bot API; error network tidak boleh menghentikan gateway."""
    try:
        response = requests.post(f"{api}/{method}", json=params, timeout=timeout)
        payload = response.json()
        if response.ok and payload.get("ok"):
            return payload
        description = str(payload.get("description", "HTTP error"))[:160] if isinstance(payload, dict) else "HTTP error"
        print(f"  [telegram] {method} gagal: {description}", flush=True)
    except (requests.RequestException, ValueError) as exc:
        print(f"  [telegram] {method} gagal: {exc.__class__.__name__}", flush=True)
    return None


def _split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Pecah respons tanpa memotong karakter secara kasar bila memungkinkan."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts or ["(jawaban kosong)"]


def _load_offset() -> int:
    try:
        data = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
        return max(0, int(data.get("offset", 0)))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def _save_offset(offset: int) -> None:
    config.ensure_data_dirs()
    temporary = OFFSET_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"offset": offset}) + "\n", encoding="utf-8")
    temporary.replace(OFFSET_FILE)


def _allowed(chat_id: int, allowed: list[Any]) -> bool:
    """Allowlist kosong = public. Nilai disamakan sebagai string agar JSON aman."""
    return not allowed or str(chat_id) in {str(item) for item in allowed}


def start(sessions, cfg: dict[str, Any], stop_event) -> None:
    token = str(cfg["token"]).strip()
    api = API_TEMPLATE.format(token=token)
    allowed = cfg.get("allowed", [])
    tool_profile = str(cfg.get("tool_profile", "safe"))

    me = _api_call(api, "getMe", timeout=20)
    if not me:
        print("  [telegram] token tidak bisa diverifikasi; gateway dihentikan.", flush=True)
        return
    username = str((me.get("result") or {}).get("username", "?"))
    print(f"  [telegram] @{username} terhubung via polling", flush=True)

    offset = _load_offset()
    while not stop_event.is_set():
        try:
            response = requests.get(
                f"{api}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message"])},
                timeout=35,
            )
            payload = response.json()
            if not response.ok or not payload.get("ok"):
                description = str(payload.get("description", f"HTTP {response.status_code}"))[:160]
                print(f"  [telegram] getUpdates gagal: {description}", flush=True)
                stop_event.wait(3)
                continue
        except requests.Timeout:
            continue  # Long polling normal.
        except (requests.RequestException, ValueError) as exc:
            print(f"  [telegram] polling error: {exc.__class__.__name__}", flush=True)
            stop_event.wait(3)
            continue

        for update in payload.get("result", []):
            update_id = int(update.get("update_id", -1))
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            text = str(message.get("text") or "").strip()

            try:
                if chat_id is not None and text:
                    if not _allowed(int(chat_id), allowed):
                        _api_call(api, "sendMessage", chat_id=chat_id, text="Akses bot ini belum diizinkan.")
                    elif text in {"/start", "/help"}:
                        _api_call(
                            api,
                            "sendMessage",
                            chat_id=chat_id,
                            text=(
                                "Aesora siap. Kirim pesan biasa untuk mulai.\n\n"
                                "/new — mulai percakapan baru\n"
                                "/status — cek status sesi"
                            ),
                        )
                    elif text == "/new":
                        sessions.reset(f"telegram:{chat_id}")
                        _api_call(api, "sendMessage", chat_id=chat_id, text="Percakapan baru dimulai.")
                    elif text == "/status":
                        _api_call(api, "sendMessage", chat_id=chat_id, text=f"Aesora aktif. Session cache: {sessions.count()}.")
                    elif not text.startswith("/"):
                        _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")

                        def on_tool(_name, _args):
                            _api_call(api, "sendChatAction", chat_id=chat_id, action="typing")

                        try:
                            reply = sessions.send(
                                identity=f"telegram:{chat_id}",
                                text=text,
                                tool_profile=tool_profile,
                                on_tool=on_tool,
                            )
                        except AesoraError as exc:
                            reply = f"Maaf, Aesora sedang bermasalah: {exc}"
                        except Exception:
                            print("  [telegram] unhandled agent error", flush=True)
                            reply = "Maaf, terjadi error internal. Coba lagi sebentar."
                        for part in _split_message(reply):
                            _api_call(api, "sendMessage", chat_id=chat_id, text=part)
            finally:
                # Simpan setelah handler selesai agar restart tidak kehilangan update.
                offset = max(offset, update_id + 1)
                _save_offset(offset)

    print("  [telegram] berhenti", flush=True)
