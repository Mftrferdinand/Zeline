"""Autentikasi Zeline App — JWT-based, provider-agnostic.

Secrets:
- APP_SECRET (ENV atau file lokal): kunci penandatangan JWT.
- Token gateway (ENV atau config.json): token akses awal.

Provider API keys TIDAK pernah masuk ke respons chat/session/history.
Keys tetap di zeline/config.json (lokal, tidak di-commit) dan hanya
digunakan oleh agent runtime saat memanggil provider.
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Any

# Minimal JWT placeholder (tidak pakai library eksternal)
# Dalam produksi: pakai PyJWT atau jose

# Field yang aman dikirim ke client walau namanya memuat kata sensitif:
# token akses milik client itu sendiri, hint key yang sudah ter-mask, dan
# id korelasi event streaming.
SAFE_FIELDS = frozenset({
    "access_token", "token_type", "refresh_supported",
    "api_key_hint", "credential_status",
    "agent_api_token", "agent_api_token_hint",
    "tool_call_id", "code_call_id",
})


def _base64url(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign(payload: dict[str, Any], secret: bytes) -> str:
    import json, hmac, hashlib, base64
    header = _base64url(json.dumps({"alg":"HS256","typ":"JWT"}).encode())
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_b64 = _base64url(payload_json.encode())
    message = f"{header}.{payload_b64}"
    sig = hmac.new(secret, message.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{message}.{sig_b64}"


def generate_token(agent_ref: str, user_hint: str = "", secret: bytes | None = None) -> str:
    s = secret or (os.environ.get("ZELINE_APP_SECRET", "").encode() or secrets.token_bytes(32))
    payload = {
        "sub": user_hint or agent_ref,
        "agent_ref": agent_ref,
        "iat": int(time.time()),
        "gateway": "zeline_app",
        "scope": "agent:read agent:write session:read session:write message:send",
    }
    return _sign(payload, s if s else secrets.token_bytes(32))


def generate_agent_api_token() -> str:
    """Generate a long-lived credential scoped to one agent profile."""
    return "zln_agent_" + secrets.token_urlsafe(32)


def verify_token(token: str, secret: bytes | None = None) -> dict[str, Any] | None:
    import json, base64, hmac, hashlib
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        msg = f"{parts[0]}.{parts[1]}".encode()
        sig = base64.urlsafe_b64decode(parts[2] + "=" * (4 - len(parts[2]) % 4))
        s = secret or (os.environ.get("ZELINE_APP_SECRET", "").encode() or secrets.token_bytes(32))
        expected_sig = hmac.new(s, msg, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_json = base64.urlsafe_b64decode(parts[1] + "=" * (4 - len(parts[1]) % 4)).decode()
        payload = json.loads(payload_json)
        return payload
    except Exception:
        return None


def sanitize_for_client(agent: dict[str, Any]) -> dict[str, Any]:
    """Hapus semua field yang bisa mengandung rahasia dari respons.

    ``SAFE_FIELDS`` adalah pengecualian yang memang harus sampai ke client:
    token akses milik client sendiri, hint key yang sudah ter-mask, dan id
    korelasi event. Tanpa whitelist ini, pencarian substring akan ikut
    membuang ``access_token``/``api_key_hint`` yang justru dibutuhkan UI.
    """
    clean = dict(agent)
    for key in ("api_key", "token", "secret", "password", "credential", "provider_key"):
        if key not in SAFE_FIELDS:
            clean.pop(key, None)
            clean.pop(key.lower(), None)
        for k in list(clean):
            if isinstance(k, str) and key in k.lower() and k not in SAFE_FIELDS and k not in {"agent_api_token", "agent_api_token_hint"}:
                clean.pop(k, None)
    return clean
