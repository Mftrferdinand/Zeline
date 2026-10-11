"""Tests for zeline/oauth.py — one-click OAuth framework (all mocked)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from zeline import oauth


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ZELINE_OAUTH_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("ZELINE_OAUTH_GITHUB_CLIENT_SECRET", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_pending():
    oauth._PENDING.clear()
    yield
    oauth._PENDING.clear()


def _set_env(monkeypatch, provider="GITHUB", cid="cid123", secret="sec456"):
    monkeypatch.setenv(f"ZELINE_OAUTH_{provider}_CLIENT_ID", cid)
    if secret is not None:
        monkeypatch.setenv(f"ZELINE_OAUTH_{provider}_CLIENT_SECRET", secret)


# -- PKCE --------------------------------------------------------------------


def test_pkce_pair_format():
    verifier, challenge = oauth._pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge  # no padding


# -- provider registry --------------------------------------------------------


def test_supports_oauth():
    for p in ("github", "google", "slack", "discord"):
        assert oauth.supports_oauth(p)
    assert not oauth.supports_oauth("trello")
    assert not oauth.supports_oauth("nope")


# -- app credentials ----------------------------------------------------------


def test_app_credentials_from_env(fake_home, monkeypatch):
    _set_env(monkeypatch)
    assert oauth.app_credentials("github") == ("cid123", "sec456")


def test_app_credentials_missing(fake_home):
    assert oauth.app_credentials("github") == ("", None)


def test_save_app_credentials_0600(fake_home):
    path = oauth.save_app_credentials("github", "cid", "sec")
    assert path.is_file()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oauth.app_credentials("github") == ("cid", "sec")


def test_save_app_credentials_no_secret(fake_home):
    oauth.save_app_credentials("slack", "cid-only", None)
    assert oauth.app_credentials("slack") == ("cid-only", None)


# -- start flow ----------------------------------------------------------------


def test_start_oauth_flow_url(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:9999/callback")
    parts = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parts.query)
    assert parts.netloc == "github.com"
    assert q["client_id"] == ["cid123"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"][0]  # non-empty
    assert q["state"][0]  # non-empty
    assert q["redirect_uri"] == ["http://127.0.0.1:9999/callback"]
    # pending flow registered
    assert q["state"][0] in oauth._PENDING


def test_start_oauth_flow_unknown_provider(fake_home):
    with pytest.raises(ValueError):
        oauth.start_oauth_flow("trello", "http://127.0.0.1:1/callback")


def test_start_oauth_flow_missing_credentials(fake_home):
    with pytest.raises(ValueError, match="not configured"):
        oauth.start_oauth_flow("github", "http://127.0.0.1:1/callback")


def test_start_oauth_flow_custom_scopes(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:9/callback", scopes=["read:user"])
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["scope"] == ["read:user"]


def test_start_oauth_flow_rejects_non_localhost_redirect(fake_home, monkeypatch):
    _set_env(monkeypatch)
    for bad in ("https://evil.example/cb", "http://example.com/callback", "http://x/"):
        with pytest.raises(ValueError, match="localhost"):
            oauth.start_oauth_flow("github", bad)


# -- callback ------------------------------------------------------------------


def _fake_token_response(payload, status=200):
    resp = mock.Mock()
    resp.status_code = status
    body = json.dumps(payload).encode()
    resp.content = body
    resp.json.return_value = payload
    resp.iter_content.return_value = [body]  # stream=True path
    resp.close.return_value = None
    return resp


def test_callback_unknown_state(fake_home):
    ok, msg = oauth.handle_oauth_callback("code123", "bogus-state")
    assert not ok
    assert "start over" in msg


def test_callback_provider_error(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:1/callback")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    ok, msg = oauth.handle_oauth_callback(None, state, error="access_denied")
    assert not ok
    assert "access_denied" in msg


def test_callback_success_stores_tokens(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:1/callback")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    payload = {
        "access_token": "tok-abc",
        "token_type": "Bearer",
        "scope": "read:user",
        "refresh_token": "ref-xyz",
        "expires_in": 3600,
    }
    with mock.patch.object(oauth.requests, "post", return_value=_fake_token_response(payload)) as post:
        ok, msg = oauth.handle_oauth_callback("authcode", state)
    assert ok, msg
    # PKCE verifier + client_secret sent
    sent = post.call_args[1]["data"]
    assert sent["code_verifier"]
    assert sent["client_secret"] == "sec456"
    assert sent["grant_type"] == "authorization_code"
    # stored via connectors.store (0600)
    from zeline.connectors import store

    stored = store.load("github")
    assert stored["auth_kind"] == "oauth2"
    assert stored["access_token"] == "tok-abc"
    assert stored["refresh_token"] == "ref-xyz"
    assert stored["expires_at"] > time.time()
    assert "tok-abc" not in msg  # never leak token in message
    cred_path = fake_home / ".zeline" / "connectors" / "github.json"
    assert oct(cred_path.stat().st_mode & 0o777) == "0o600"
    # state is single-use
    ok2, _ = oauth.handle_oauth_callback("authcode", state)
    assert not ok2


def test_callback_state_expired(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:1/callback")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    oauth._PENDING[state]["created"] = time.time() - oauth._STATE_TTL - 1
    ok, _ = oauth.handle_oauth_callback("code", state)
    assert not ok


def test_callback_exchange_failure(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:1/callback")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    with mock.patch.object(oauth.requests, "post", return_value=_fake_token_response({"error": "bad_verification_code"}, status=400)):
        ok, msg = oauth.handle_oauth_callback("badcode", state)
    assert not ok
    assert "HTTP 400" in msg


def test_callback_no_code(fake_home, monkeypatch):
    _set_env(monkeypatch)
    url = oauth.start_oauth_flow("github", "http://127.0.0.1:1/callback")
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    ok, _ = oauth.handle_oauth_callback(None, state)
    assert not ok


# -- refresh -------------------------------------------------------------------


def test_refresh_fresh_token_noop(fake_home):
    from zeline.connectors import store

    store.save("github", {"auth_kind": "oauth2", "access_token": "t", "expires_at": int(time.time()) + 9999})
    assert oauth.refresh_if_needed("github") is True


def test_refresh_expired_with_refresh_token(fake_home, monkeypatch):
    from zeline.connectors import store

    _set_env(monkeypatch)
    store.save("github", {
        "auth_kind": "oauth2", "provider": "github",
        "access_token": "old", "refresh_token": "ref",
        "expires_at": int(time.time()) - 10,
    })
    with mock.patch.object(oauth.requests, "post",
                           return_value=_fake_token_response({"access_token": "new", "expires_in": 3600})):
        assert oauth.refresh_if_needed("github") is True
    assert store.load("github")["access_token"] == "new"


def test_refresh_not_oauth(fake_home):
    from zeline.connectors import store

    store.save("github", {"token": "pat-123"})
    assert oauth.refresh_if_needed("github") is False


# -- callback page ---------------------------------------------------------------


def test_callback_html_escapes():
    page = oauth.callback_html(False, "<script>alert(1)</script>")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


# -- local one-shot server ---------------------------------------------------------


def test_wait_for_callback_roundtrip():
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), oauth._OneShotHandler)
    port = server.server_address[1]

    def _hit():
        time.sleep(0.3)
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=abc&state=st1", timeout=5).read()

    t = threading.Thread(target=_hit, daemon=True)
    t.start()
    params = oauth._wait_for_callback(server, timeout=10)
    t.join(timeout=5)
    assert params is not None
    assert params["code"] == "abc"
    assert params["state"] == "st1"


def test_wait_for_callback_timeout():
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), oauth._OneShotHandler)
    assert oauth._wait_for_callback(server, timeout=1) is None
