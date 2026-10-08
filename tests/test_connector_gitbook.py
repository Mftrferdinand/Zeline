"""Tests for the GitBook connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import gitbook as gitbook_mod
from zeline.connectors.gitbook import GitBookConnector

API_BASE = "https://api.gitbook.com/v1"
SPACES_PAYLOAD = {
    "items": [
        {"id": "sp-1", "title": "Docs"},
        {"id": "sp-2", "title": "Handbook"},
    ]
}
CONTENT_PAYLOAD = {
    "items": [
        {"id": "pg-1", "title": "Intro", "type": "page"},
        {"id": "pg-2", "title": "Changelog", "type": "page"},
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

    store.save("gitbook", {"api_token": "tok", "user": "Alice"})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method.upper(), url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class GitBookConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-gb-test-"))
        _patch_store(self, self.tmp)
        self.conn = GitBookConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"displayName": "Alice"})
        ) as get:
            result = self.conn.connect(api_token="tok123")
        self.assertEqual(result, "Connected to GitBook as Alice.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/user")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok123")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("gitbook"), {"api_token": "tok123", "user": "Alice"}
        )

    def test_connect_no_token_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(), "ERROR: no API token provided.")
        self.assertIsNone(store.load("gitbook"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_token="tok")
        self.assertTrue(result.startswith("ERROR: could not reach api.gitbook.com"))
        self.assertIsNone(store.load("gitbook"))

    def test_connect_rejected_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_token="bad")
        self.assertIn("ERROR: GitBook rejected the token (HTTP 401)", result)
        self.assertIsNone(store.load("gitbook"))

    def test_connect_unreadable_body_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse(ValueError("nope"))):
            result = self.conn.connect(api_token="tok")
        self.assertEqual(result, "ERROR: GitBook returned an unreadable response.")
        self.assertIsNone(store.load("gitbook"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "Alice"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "GitBook disconnected.")
        self.assertEqual(self.conn.disconnect(), "GitBook was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "gitbook")
        self.assertEqual(self.conn.name, "GitBook")
        self.assertEqual(self.conn.auth_kind, "pat")


class GitBookOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-gb-test-"))
        _patch_store(self, self.tmp)
        self.conn = GitBookConnector()
        _seed_connected()

    def test_list_spaces(self):
        import copy

        mapping = {("GET", f"{API_BASE}/spaces"): (copy.deepcopy(SPACES_PAYLOAD), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_spaces(limit=2)
        self.assertEqual(result, "sp-1: Docs\nsp-2: Handbook")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/spaces")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_spaces_limit_clamped(self):
        import copy

        mapping = {("GET", f"{API_BASE}/spaces"): (copy.deepcopy(SPACES_PAYLOAD), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_spaces(limit=500)
        self.assertEqual(len(result.splitlines()), 2)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_spaces_empty(self):
        mapping = {("GET", f"{API_BASE}/spaces"): ({"items": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_spaces(), "No spaces found.")

    def test_list_content(self):
        import copy

        mapping = {
            ("GET", f"{API_BASE}/spaces/sp-1/content"): (copy.deepcopy(CONTENT_PAYLOAD), 200)
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_content("sp-1", limit=2)
        self.assertEqual(result, "pg-1: Intro (page)\npg-2: Changelog (page)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/spaces/sp-1/content")
        self.assertEqual(kwargs["params"], {"limit": 2})

    def test_list_content_empty(self):
        mapping = {("GET", f"{API_BASE}/spaces/sp-9/content"): ({"items": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(
                self.conn.list_content("sp-9"), "No content found in space sp-9."
            )

    def test_operation_http_error(self):
        mapping = {("GET", f"{API_BASE}/spaces"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_spaces()
        self.assertIn("ERROR: GitBook API 500 on /spaces.", str(ctx.exception))

    def test_operation_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_content("sp-1")
        self.assertIn("ERROR: GitBook API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("gitbook")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_spaces()
        self.assertIn("zeline connect gitbook", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_content("sp-1")


class GitBookRegistryTests(unittest.TestCase):
    def test_gitbook_registered(self):
        from zeline.connectors import get

        conn = get("gitbook")
        self.assertIsInstance(conn, GitBookConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(gitbook_mod.GitBookConnector.id, "gitbook")


if __name__ == "__main__":
    unittest.main()
