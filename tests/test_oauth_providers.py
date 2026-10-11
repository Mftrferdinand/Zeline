"""Tests for the OAuth expansion providers: notion, linear, gitlab, spotify, x.

unittest style (per CONTRIBUTING: CI runs `python -m unittest discover`).
All network calls are mocked — these tests verify the registry config,
the authorization URLs, and the token-exchange wiring (Basic auth / JSON
body per provider) without touching the real providers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import oauth  # noqa: E402

NEW_PROVIDERS = ("notion", "linear", "gitlab", "spotify", "x")


def _fake_token_response(payload, status=200):
    resp = mock.Mock()
    resp.status_code = status
    body = json.dumps(payload).encode()
    resp.iter_content.return_value = [body]
    resp.close.return_value = None
    return resp


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._home_patch = mock.patch.object(
            Path, "home", classmethod(lambda cls: Path(self._tmp.name))
        )
        self._home_patch.start()
        oauth._PENDING.clear()
        # bersihkan env kredensial provider agar isolasi antar test
        self._saved_env = {}
        for p in NEW_PROVIDERS:
            for kind in ("CLIENT_ID", "CLIENT_SECRET"):
                key = f"ZELINE_OAUTH_{p.upper()}_{kind}"
                self._saved_env[key] = __import__("os").environ.pop(key, None)

    def tearDown(self):
        import os

        for key, val in self._saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._home_patch.stop()
        oauth._PENDING.clear()
        self._tmp.cleanup()

    def set_creds(self, provider, cid="cid123", secret="sec456"):
        import os

        os.environ[f"ZELINE_OAUTH_{provider.upper()}_CLIENT_ID"] = cid
        os.environ[f"ZELINE_OAUTH_{provider.upper()}_CLIENT_SECRET"] = secret

    def start(self, provider):
        """Build the auth URL; return (netloc, query dict)."""
        url = oauth.start_oauth_flow(provider, "http://127.0.0.1:4321/callback")
        parts = urllib.parse.urlparse(url)
        return parts.netloc, urllib.parse.parse_qs(parts.query)


class RegistryTests(_Base):
    def test_new_providers_registered(self):
        expected = {
            "notion": ("https://api.notion.com/v1/oauth/authorize",
                       "https://api.notion.com/v1/oauth/token"),
            "linear": ("https://linear.app/oauth/authorize",
                       "https://api.linear.app/oauth/token"),
            "gitlab": ("https://gitlab.com/oauth/authorize",
                       "https://gitlab.com/oauth/token"),
            "spotify": ("https://accounts.spotify.com/authorize",
                        "https://accounts.spotify.com/api/token"),
            "x": ("https://twitter.com/i/oauth2/authorize",
                  "https://api.twitter.com/2/oauth2/token"),
        }
        for provider, (auth, token) in expected.items():
            with self.subTest(provider=provider):
                cfg = oauth.OAUTH_PROVIDERS[provider]
                self.assertEqual(cfg["auth_endpoint"], auth)
                self.assertEqual(cfg["token_endpoint"], token)
                # endpoint harus https — tidak ada downgrade ke http
                self.assertTrue(cfg["auth_endpoint"].startswith("https://"))
                self.assertTrue(cfg["token_endpoint"].startswith("https://"))
                self.assertIs(cfg["needs_secret"], True)
                self.assertIsInstance(cfg["scopes"], list)
                self.assertTrue(oauth.supports_oauth(provider))

    def test_provider_names_match_allowed_regex(self):
        for provider in NEW_PROVIDERS:
            with self.subTest(provider=provider):
                self.assertIsNotNone(re.fullmatch(r"[a-zA-Z0-9_-]+", provider))

    def test_notion_flags(self):
        cfg = oauth.OAUTH_PROVIDERS["notion"]
        self.assertIs(cfg["token_basic_auth"], True)
        self.assertIs(cfg["token_json_body"], True)
        self.assertEqual(cfg["scopes"], [])

    def test_basic_auth_providers(self):
        for provider in ("notion", "spotify", "x"):
            with self.subTest(provider=provider):
                self.assertIs(oauth.OAUTH_PROVIDERS[provider]["token_basic_auth"], True)


class AuthUrlTests(_Base):
    def test_auth_url_domains_and_pkce(self):
        domains = {
            "notion": "api.notion.com",
            "linear": "linear.app",
            "gitlab": "gitlab.com",
            "spotify": "accounts.spotify.com",
            "x": "twitter.com",
        }
        for provider, domain in domains.items():
            with self.subTest(provider=provider):
                self.set_creds(provider)
                netloc, q = self.start(provider)
                self.assertEqual(netloc, domain)
                self.assertEqual(q["client_id"], ["cid123"])
                self.assertEqual(q["response_type"], ["code"])
                # PKCE S256 selalu dikirim — wajib untuk X
                self.assertEqual(q["code_challenge_method"], ["S256"])
                self.assertTrue(q["code_challenge"][0])
                self.assertIn(q["state"][0], oauth._PENDING)

    def test_x_pkce_verifier_matches_challenge(self):
        """X mandates PKCE S256: the verifier in the pending flow must
        derive the challenge sent in the URL."""
        self.set_creds("x")
        netloc, q = self.start("x")
        verifier = oauth._PENDING[q["state"][0]]["verifier"]
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], [expected])
        self.assertEqual(q["scope"], ["tweet.read users.read"])

    def test_notion_auth_url_params(self):
        self.set_creds("notion")
        _, q = self.start("notion")
        # owner=user wajib untuk Notion
        self.assertEqual(q["owner"], ["user"])
        # Notion tidak pakai scope — param harus dihilangkan, bukan dikirim kosong
        self.assertNotIn("scope", q)

    def test_linear_and_gitlab_scopes(self):
        self.set_creds("linear")
        _, q = self.start("linear")
        self.assertEqual(q["scope"], ["read"])
        self.set_creds("gitlab")
        _, q = self.start("gitlab")
        self.assertEqual(q["scope"], ["read_user read_api"])


class ExchangeTests(_Base):
    def _do_flow(self, provider, payload=None):
        from zeline.connectors import store

        self.set_creds(provider)
        url = oauth.start_oauth_flow(provider, "http://127.0.0.1:4321/callback")
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        payload = payload or {
            "access_token": f"tok-{provider}",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        with mock.patch.object(oauth.requests, "post",
                               return_value=_fake_token_response(payload)) as post:
            ok, msg = oauth.handle_oauth_callback("authcode", state)
        self.assertTrue(ok, msg)
        return post, msg, store

    def test_spotify_exchange_uses_basic_auth(self):
        post, msg, store = self._do_flow("spotify")
        kwargs = post.call_args[1]
        # HTTP Basic auth — secret tidak di body
        self.assertEqual(kwargs["auth"], ("cid123", "sec456"))
        sent = kwargs["data"]
        self.assertNotIn("client_secret", sent)
        self.assertNotIn("client_id", sent)
        self.assertEqual(sent["grant_type"], "authorization_code")
        self.assertTrue(sent["code_verifier"])
        stored = store.load("spotify")
        self.assertEqual(stored["auth_kind"], "oauth2")
        self.assertEqual(stored["access_token"], "tok-spotify")
        self.assertNotIn("tok-spotify", msg)
        cred_path = self.home / ".zeline" / "connectors" / "spotify.json"
        self.assertEqual(oct(cred_path.stat().st_mode & 0o777), "0o600")

    def test_x_exchange_uses_basic_auth(self):
        post, msg, store = self._do_flow("x")
        kwargs = post.call_args[1]
        self.assertEqual(kwargs["auth"], ("cid123", "sec456"))
        self.assertNotIn("client_secret", kwargs["data"])
        stored = store.load("x")
        self.assertEqual(stored["access_token"], "tok-x")
        cred_path = self.home / ".zeline" / "connectors" / "x.json"
        self.assertEqual(oct(cred_path.stat().st_mode & 0o777), "0o600")

    def test_notion_exchange_json_body_and_basic_auth(self):
        post, msg, store = self._do_flow(
            "notion",
            payload={"access_token": "tok-notion", "bot_id": "b1", "workspace_id": "w1"},
        )
        kwargs = post.call_args[1]
        self.assertEqual(kwargs["auth"], ("cid123", "sec456"))
        # body JSON, bukan form
        self.assertIn("json", kwargs)
        self.assertNotIn("data", kwargs)
        body = kwargs["json"]
        self.assertEqual(body["grant_type"], "authorization_code")
        self.assertNotIn("client_secret", body)
        self.assertNotIn("client_id", body)
        self.assertTrue(body["code_verifier"])
        stored = store.load("notion")
        self.assertEqual(stored["access_token"], "tok-notion")
        self.assertNotIn("tok-notion", msg)
        cred_path = self.home / ".zeline" / "connectors" / "notion.json"
        self.assertEqual(oct(cred_path.stat().st_mode & 0o777), "0o600")

    def test_linear_and_gitlab_exchange_form_body(self):
        for provider in ("linear", "gitlab"):
            with self.subTest(provider=provider):
                post, msg, store = self._do_flow(provider)
                kwargs = post.call_args[1]
                # tanpa basic auth -> secret tetap di body (form-encoded)
                self.assertIsNone(kwargs.get("auth"))
                sent = kwargs["data"]
                self.assertEqual(sent["client_secret"], "sec456")
                self.assertEqual(sent["grant_type"], "authorization_code")
                stored = store.load(provider)
                self.assertEqual(stored["access_token"], f"tok-{provider}")
                cred_path = self.home / ".zeline" / "connectors" / f"{provider}.json"
                self.assertEqual(oct(cred_path.stat().st_mode & 0o777), "0o600")

    def test_refresh_uses_basic_auth_for_x(self):
        from zeline.connectors import store

        import time

        self.set_creds("x")
        store.save("x", {
            "auth_kind": "oauth2", "provider": "x",
            "access_token": "old", "refresh_token": "ref",
            "expires_at": int(time.time()) - 10,
        })
        with mock.patch.object(oauth.requests, "post",
                               return_value=_fake_token_response(
                                   {"access_token": "new", "expires_in": 7200})) as post:
            self.assertTrue(oauth.refresh_if_needed("x"))
        kwargs = post.call_args[1]
        self.assertEqual(kwargs["auth"], ("cid123", "sec456"))
        self.assertNotIn("client_secret", kwargs["data"])
        self.assertEqual(store.load("x")["access_token"], "new")

    def test_state_single_use_per_provider(self):
        self.set_creds("gitlab")
        url = oauth.start_oauth_flow("gitlab", "http://127.0.0.1:4321/callback")
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        with mock.patch.object(oauth.requests, "post",
                               return_value=_fake_token_response({"access_token": "t"})):
            ok, _ = oauth.handle_oauth_callback("code", state)
        self.assertTrue(ok)
        ok2, msg2 = oauth.handle_oauth_callback("code", state)
        self.assertFalse(ok2)
        self.assertIn("start over", msg2)


class ProviderNameValidationTests(_Base):
    def test_reject_weird_provider_names(self):
        for bad in ("../evil", "a b", "a;b", "a'b", 'a"b', "a/b", "", "x\nid"):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    oauth.save_app_credentials(bad, "cid", "sec")


if __name__ == "__main__":
    unittest.main()
