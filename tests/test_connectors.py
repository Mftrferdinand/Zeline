"""Tests for the connectors framework + GitHub connector (phase 1). All HTTP mocked."""
from __future__ import annotations

import importlib
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline.connectors import base, oauth, store
from zeline.connectors import github as github_mod
from zeline import connectors as connectors_pkg


def fresh_home(test):
    """Redirect Path.home() (used by the credential store) into a tmp dir."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    patcher = mock.patch("pathlib.Path.home", return_value=Path(tmp.name))
    patcher.start()
    test.addCleanup(patcher.stop)
    return Path(tmp.name)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class StoreTests(unittest.TestCase):
    def setUp(self):
        fresh_home(self)

    def test_roundtrip(self):
        store.save("github", {"token": "abc", "login": "octo"})
        self.assertEqual(store.load("github"), {"token": "abc", "login": "octo"})

    @unittest.skipIf(os.name == "nt", "POSIX permission bits don't apply on Windows")
    def test_chmod_600(self):
        path = store.save("github", {"token": "abc"})
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_load_missing_returns_none(self):
        self.assertIsNone(store.load("nope"))

    def test_delete(self):
        store.save("github", {"token": "abc"})
        self.assertTrue(store.delete("github"))
        self.assertFalse(store.delete("github"))
        self.assertIsNone(store.load("github"))

    def test_load_corrupt_returns_none(self):
        store.store_path("github").write_text("{not json", encoding="utf-8")
        self.assertIsNone(store.load("github"))


class RegistryTests(unittest.TestCase):
    def test_github_registered(self):
        conn = connectors_pkg.get("github")
        self.assertIsNotNone(conn)
        self.assertIsInstance(conn, base.BaseConnector)
        self.assertEqual(conn.id, "github")

    def test_unknown_returns_none(self):
        self.assertIsNone(connectors_pkg.get("does-not-exist"))

    def test_all_ids_sorted(self):
        ids = connectors_pkg.all_ids()
        self.assertEqual(ids, sorted(ids))
        self.assertIn("github", ids)


class OAuthTests(unittest.TestCase):
    def test_build_auth_url(self):
        url = oauth.build_auth_url(
            "https://example.com/auth", "cid123", "http://127.0.0.1:9/cb",
            ["mail.read", "cal"], "state42",
        )
        self.assertIn("client_id=cid123", url)
        self.assertIn("response_type=code", url)
        self.assertIn("state=state42", url)
        self.assertIn("scope=mail.read", url)
        self.assertIn("redirect_uri=", url)

    def test_exchange_code(self):
        payload = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        fake = mock.Mock(status_code=200)
        fake.json.return_value = dict(payload)
        with mock.patch("requests.post", return_value=fake) as post:
            data = oauth.exchange_code("https://example.com/tok", "cid", "sec", "code1", "http://x/cb")
        post.assert_called_once()
        self.assertEqual(data["access_token"], "at")
        self.assertIn("expires_at", data)
        self.assertGreater(data["expires_at"], int(time.time()))

    def test_refresh_access_token_keeps_old_refresh(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"access_token": "new-at", "expires_in": 60}
        with mock.patch("requests.post", return_value=fake):
            data = oauth.refresh_access_token("https://example.com/tok", "cid", "sec", "old-rt")
        self.assertEqual(data["access_token"], "new-at")
        self.assertEqual(data["refresh_token"], "old-rt")

    @unittest.skipIf(
        sys.platform == "darwin",
        "GitHub macOS runners blackhole loopback TCP connects "
        "(SYNs to 127.0.0.1 get no response at all); covered on Linux/Windows CI.",
    )
    def test_run_local_callback(self):
        import http.client

        port = _free_port()
        result: dict = {}
        thread = threading.Thread(
            target=lambda: result.update(code=oauth.run_local_callback(port, timeout=30)),
            daemon=True,
        )
        thread.start()
        # Poll until the loopback listener accepts: some CI runners (notably
        # macOS) are slow to bring it up, and urllib honours proxy env/system
        # settings which can blackhole localhost. http.client bypasses all of
        # that machinery.
        deadline = time.monotonic() + 25
        while True:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/?code=abc123")
                conn.getresponse().read()
                conn.close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        thread.join(35)
        self.assertEqual(result.get("code"), "abc123")

    def test_session_valid_token_no_refresh_needed(self):
        sess = oauth.OAuth2Session("https://example.com/tok", "cid", "sec",
                                   {"access_token": "live", "expires_at": int(time.time()) + 9999})
        self.assertEqual(sess.valid_token(), "live")

    def test_session_auto_refresh(self):
        new = {"access_token": "fresh", "expires_in": 3600, "refresh_token": "rt"}
        with mock.patch.object(oauth, "refresh_access_token", return_value=dict(new)) as rf:
            sess = oauth.OAuth2Session("https://example.com/tok", "cid", "sec",
                                       {"access_token": "old", "refresh_token": "rt",
                                        "expires_at": int(time.time()) - 10})
            self.assertEqual(sess.valid_token(), "fresh")
        rf.assert_called_once()

    def test_session_no_token(self):
        sess = oauth.OAuth2Session("https://example.com/tok", "cid", "sec", {})
        self.assertIsNone(sess.valid_token())
        self.assertEqual(sess.auth_header(), {})


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


class GitHubConnectorTests(unittest.TestCase):
    def setUp(self):
        fresh_home(self)
        self.conn = github_mod.GitHubConnector()

    def test_connect_success_stores_credential(self):
        with mock.patch("requests.get", return_value=_FakeResp(200, {"login": "octo"})):
            result = self.conn.connect(token="tok123")
        self.assertIn("octo", result)
        self.assertNotIn("ERROR", result)
        saved = store.load("github")
        self.assertEqual(saved["login"], "octo")
        self.assertEqual(saved["token"], "tok123")
        self.assertTrue(self.conn.is_connected())
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "@octo"})

    def test_connect_rejected_token_not_stored(self):
        with mock.patch("requests.get", return_value=_FakeResp(401, {})):
            result = self.conn.connect(token="bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("github"))
        self.assertFalse(self.conn.is_connected())

    def test_connect_empty_token(self):
        self.assertTrue(self.conn.connect(token="").startswith("ERROR:"))

    def test_disconnect(self):
        store.save("github", {"token": "x", "login": "y"})
        self.assertEqual(self.conn.disconnect(), "GitHub disconnected.")
        self.assertFalse(self.conn.is_connected())

    def _connected(self):
        store.save("github", {"token": "tok", "login": "octo"})

    def test_list_repos_format(self):
        self._connected()
        payload = [
            {"full_name": "octo/a", "description": "Alpha", "stargazers_count": 5},
            {"full_name": "octo/b", "description": None, "stargazers_count": 0},
        ]
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.list_repos(limit=2)
        self.assertIn("octo/a — Alpha (★5)", out)
        self.assertIn("octo/b (★0)", out)

    def test_list_issues_format(self):
        self._connected()
        payload = [
            {"number": 3, "title": "Bug here", "labels": [{"name": "bug"}]},
            {"number": 4, "title": "A PR", "labels": [], "pull_request": {}},
        ]
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.list_issues("octo", "a")
        self.assertIn("#3 Bug here [bug]", out)
        self.assertNotIn("#4", out)

    def test_create_issue_format(self):
        self._connected()
        payload = {"number": 7, "html_url": "https://github.com/octo/a/issues/7"}
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.create_issue("octo", "a", "Title", "Body")
        self.assertEqual(out, "#7 https://github.com/octo/a/issues/7")

    def test_comment_issue_format(self):
        self._connected()
        payload = {"html_url": "https://github.com/octo/a/issues/7#issuecomment-1"}
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.comment_issue("octo", "a", 7, "hi")
        self.assertIn("Comment posted:", out)

    def test_list_prs_format(self):
        self._connected()
        payload = [{"number": 9, "title": "Fix", "head": {"ref": "feat"}, "base": {"ref": "main"}}]
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.list_prs("octo", "a")
        self.assertIn("#9 Fix (feat→main)", out)

    def test_api_error_wrapped(self):
        self._connected()
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(404, {})):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_repos()
        self.assertIn("ERROR:", str(ctx.exception))

    def test_no_secret_in_status(self):
        self._connected()
        blob = json.dumps(self.conn.status())
        self.assertNotIn("tok", blob)


class ToolHandlerTests(unittest.TestCase):
    def setUp(self):
        fresh_home(self)
        self.tools = importlib.import_module("zeline.tools")

    def test_tool_defs_registered(self):
        names = {d.name: d for d in self.tools.TOOL_DEFS}
        for name in ("github_repos", "github_issues", "github_create_issue",
                     "github_issue_comment", "github_prs"):
            self.assertIn(name, names)
            self.assertEqual(names[name].profiles, frozenset({"workspace", "full"}))

    def test_handler_disconnected_message(self):
        out = self.tools._connector_tool("github", "list_repos", limit=5)
        self.assertIn("zeline connect github", out)
        self.assertTrue(out.startswith("ERROR:"))

    def test_handler_unknown_connector(self):
        out = self.tools._connector_tool("nope", "list_repos")
        self.assertIn("zeline connect nope", out)

    def test_handler_connected_calls_through(self):
        store.save("github", {"token": "tok", "login": "octo"})
        payload = [{"full_name": "octo/a", "description": "d", "stargazers_count": 1}]
        with mock.patch("zeline.connectors.github.requests.request",
                        return_value=_FakeResp(200, payload)):
            out = self.tools._connector_tool("github", "list_repos", limit=5)
        self.assertIn("octo/a", out)


if __name__ == "__main__":
    unittest.main()
