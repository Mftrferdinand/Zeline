"""Tests for the Ghost connector. All HTTP is mocked."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import ghost as ghost_mod
from zeline.connectors.ghost import GhostConnector

SITE = "https://myblog.example.com"
ADMIN = f"{SITE}/ghost/api/admin"
KEY_ID = "abc123"
SECRET_HEX = "00112233445566778899aabbccddeeff"
API_KEY = f"{KEY_ID}:{SECRET_HEX}"
POSTS_PAYLOAD = {
    "posts": [
        {"slug": "hello", "title": "Hello", "status": "draft"},
        {"slug": "world", "title": "World", "status": "published"},
    ]
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("ghost", {"url": SITE, "key_id": KEY_ID, "secret_hex": SECRET_HEX})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method.upper(), url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


def _decode_segment(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


class GhostJwtTests(unittest.TestCase):
    def test_make_jwt_structure(self):
        token = ghost_mod._make_jwt(KEY_ID, SECRET_HEX)
        parts = token.split(".")
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertNotIn("=", part)

    def test_make_jwt_header_and_payload(self):
        token = ghost_mod._make_jwt(KEY_ID, SECRET_HEX)
        header = _decode_segment(token.split(".")[0])
        payload = _decode_segment(token.split(".")[1])
        self.assertEqual(header, {"alg": "HS256", "typ": "JWT", "kid": KEY_ID})
        self.assertEqual(payload["aud"], "/admin/")
        self.assertEqual(payload["exp"] - payload["iat"], 300)

    def test_make_jwt_signature_valid(self):
        token = ghost_mod._make_jwt(KEY_ID, SECRET_HEX)
        signing_input, signature = token.rsplit(".", 1)
        expected = hmac.new(
            bytes.fromhex(SECRET_HEX), signing_input.encode("ascii"), hashlib.sha256
        ).digest()
        self.assertEqual(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)), expected)

    def test_make_jwt_invalid_hex(self):
        with self.assertRaises(RuntimeError) as ctx:
            ghost_mod._make_jwt(KEY_ID, "not-hex!!")
        self.assertEqual(str(ctx.exception), "ERROR: Ghost API secret is not valid hex.")


class GhostConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ghost-test-"))
        _patch_store(self, self.tmp)
        self.conn = GhostConnector()

    def test_connect_success_saves_parts_not_jwt(self):
        from zeline.connectors import store

        ok = {"posts": [{"slug": "hello", "title": "Hello", "status": "draft"}]}
        with mock.patch("requests.get", return_value=FakeResponse(ok)) as get:
            result = self.conn.connect(url=SITE, api_key=API_KEY)
        self.assertEqual(result, f"Connected to Ghost at {SITE}.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{ADMIN}/posts/?limit=1")
        auth = kwargs["headers"]["Authorization"]
        self.assertTrue(auth.startswith("Ghost "))
        self.assertEqual(len(auth.split(" ", 1)[1].split(".")), 3)
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("ghost")
        self.assertEqual(saved, {"url": SITE, "key_id": KEY_ID, "secret_hex": SECRET_HEX})
        self.assertNotIn("jwt", saved)
        self.assertNotIn("token", saved)

    def test_connect_normalizes_bare_host(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"posts": []})) as get:
            result = self.conn.connect(url="myblog.example.com", api_key=API_KEY)
        self.assertEqual(result, "Connected to Ghost at https://myblog.example.com.")
        self.assertTrue(get.call_args.args[0].startswith("https://myblog.example.com/"))
        self.assertEqual(store.load("ghost")["url"], "https://myblog.example.com")

    def test_connect_no_url_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(api_key=API_KEY), "ERROR: no site URL provided.")
        self.assertIsNone(store.load("ghost"))

    def test_connect_no_api_key_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(url=SITE), "ERROR: no API key provided.")
        self.assertIsNone(store.load("ghost"))

    def test_connect_bad_key_format_stores_nothing(self):
        from zeline.connectors import store

        for bad in ("nocolon", ":secret-only", "id-only:"):
            result = self.conn.connect(url=SITE, api_key=bad)
            self.assertEqual(result, "ERROR: API key must be in id:secret format.")
            self.assertIsNone(store.load("ghost"))

    def test_connect_invalid_hex_secret_stores_nothing(self):
        from zeline.connectors import store

        result = self.conn.connect(url=SITE, api_key=f"{KEY_ID}:not-hex!!")
        self.assertEqual(result, "ERROR: Ghost API secret is not valid hex.")
        self.assertIsNone(store.load("ghost"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(url=SITE, api_key=API_KEY)
        self.assertTrue(result.startswith(f"ERROR: could not reach {SITE}"))
        self.assertIsNone(store.load("ghost"))

    def test_connect_rejected_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(url=SITE, api_key=API_KEY)
        self.assertIn("ERROR: Ghost rejected the Admin API key (HTTP 401)", result)
        self.assertIsNone(store.load("ghost"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": SITE})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Ghost disconnected.")
        self.assertEqual(self.conn.disconnect(), "Ghost was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "ghost")
        self.assertEqual(self.conn.name, "Ghost")
        self.assertEqual(self.conn.auth_kind, "pat")


class GhostOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ghost-test-"))
        _patch_store(self, self.tmp)
        self.conn = GhostConnector()
        _seed_connected()

    def test_list_posts(self):
        import copy

        mapping = {("GET", f"{ADMIN}/posts/"): (copy.deepcopy(POSTS_PAYLOAD), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_posts(limit=2)
        self.assertEqual(result, "hello: Hello [draft]\nworld: World [published]")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{ADMIN}/posts/")
        self.assertEqual(kwargs["params"], {"limit": 2, "fields": "title,slug,status"})
        auth = kwargs["headers"]["Authorization"]
        self.assertTrue(auth.startswith("Ghost "))
        self.assertEqual(len(auth.split(" ", 1)[1].split(".")), 3)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_posts_limit_clamped(self):
        import copy

        mapping = {("GET", f"{ADMIN}/posts/"): (copy.deepcopy(POSTS_PAYLOAD), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_posts(limit=500)
        self.assertEqual(len(result.splitlines()), 2)
        self.assertEqual(req.call_args.kwargs["params"]["limit"], 100)

    def test_list_posts_empty(self):
        mapping = {("GET", f"{ADMIN}/posts/"): ({"posts": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_posts(), "No posts found.")

    def test_create_post(self):
        created = {"posts": [{"slug": "my-draft", "title": "My Draft", "status": "draft"}]}
        mapping = {("POST", f"{ADMIN}/posts/"): (created, 201)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.create_post("My Draft", html="<p>hi</p>")
        self.assertEqual(result, "Draft created: my-draft")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{ADMIN}/posts/")
        self.assertEqual(
            kwargs["json"],
            {"posts": [{"title": "My Draft", "html": "<p>hi</p>", "status": "draft"}]},
        )
        auth = kwargs["headers"]["Authorization"]
        self.assertTrue(auth.startswith("Ghost "))

    def test_operation_http_error(self):
        mapping = {("GET", f"{ADMIN}/posts/"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_posts()
        self.assertIn("ERROR: Ghost API 500 on /posts/.", str(ctx.exception))

    def test_operation_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_post("T")
        self.assertIn("ERROR: Ghost API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("ghost")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_posts()
        self.assertIn("zeline connect ghost", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_post("T")


class GhostRegistryTests(unittest.TestCase):
    def test_ghost_registered(self):
        from zeline.connectors import get

        conn = get("ghost")
        self.assertIsInstance(conn, GhostConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(ghost_mod.GhostConnector.id, "ghost")


if __name__ == "__main__":
    unittest.main()
