"""Tests for the Linear connector. All HTTP mocked."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import requests

from zeline.connectors import store
from zeline.connectors import linear as linear_mod


def fresh_home(test):
    """Redirect Path.home() (used by the credential store) into a tmp dir."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    patcher = mock.patch("pathlib.Path.home", return_value=Path(tmp.name))
    patcher.start()
    test.addCleanup(patcher.stop)
    return Path(tmp.name)


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class LinearConnectorTests(unittest.TestCase):
    def setUp(self):
        fresh_home(self)
        self.conn = linear_mod.LinearConnector()

    def _connected(self):
        store.save("linear", {"api_key": "tok123", "user": "Octo"})

    # -- connect ---------------------------------------------------------

    def test_connect_empty_key(self):
        self.assertTrue(self.conn.connect(api_key="").startswith("ERROR:"))
        self.assertIsNone(store.load("linear"))

    def test_connect_whitespace_key(self):
        self.assertTrue(self.conn.connect(api_key="   ").startswith("ERROR:"))
        self.assertIsNone(store.load("linear"))

    def test_connect_graphql_errors_not_saved(self):
        payload = {"errors": [{"message": "Invalid API key"}]}
        with mock.patch("requests.post", return_value=_FakeResp(200, payload)):
            result = self.conn.connect(api_key="bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("GraphQL", result)
        self.assertIsNone(store.load("linear"))
        self.assertFalse(self.conn.is_connected())

    def test_connect_http_error_not_saved(self):
        with mock.patch("requests.post", return_value=_FakeResp(401, {})):
            result = self.conn.connect(api_key="bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("linear"))

    def test_connect_request_exception_not_saved(self):
        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="tok123")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("linear"))

    def test_connect_success_stores_credential(self):
        payload = {"data": {"viewer": {"name": "Octo"}}}
        with mock.patch("requests.post", return_value=_FakeResp(200, payload)) as post:
            result = self.conn.connect(api_key="tok123")
        self.assertNotIn("ERROR", result)
        self.assertIn("Octo", result)
        saved = store.load("linear")
        self.assertEqual(saved["user"], "Octo")
        self.assertEqual(saved["api_key"], "tok123")
        # Raw key, no "Bearer" prefix.
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "tok123")
        self.assertNotIn("Bearer", headers["Authorization"])
        self.assertTrue(self.conn.is_connected())
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "Octo"})

    def test_connect_empty_viewer_not_saved(self):
        with mock.patch("requests.post", return_value=_FakeResp(200, {"data": {}})):
            result = self.conn.connect(api_key="tok123")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("linear"))

    # -- status / disconnect ---------------------------------------------

    def test_status_disconnected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_disconnect(self):
        self._connected()
        self.assertEqual(self.conn.disconnect(), "Linear disconnected.")
        self.assertFalse(self.conn.is_connected())
        self.assertEqual(self.conn.disconnect(), "Linear was not connected.")

    def test_no_secret_in_status_or_messages(self):
        self._connected()
        blob = json.dumps(self.conn.status())
        self.assertNotIn("tok123", blob)
        self.assertEqual(self.conn.status()["detail"], "Octo")

    # -- list_issues -----------------------------------------------------

    def test_list_issues_format(self):
        self._connected()
        payload = {"data": {"issues": {"nodes": [
            {"identifier": "ENG-123", "title": "Fix bug", "state": {"name": "Todo"}},
            {"identifier": "ENG-124", "title": "Ship it", "state": {"name": "In Progress"}},
        ]}}}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.list_issues(limit=2)
        self.assertIn("ENG-123 Fix bug [Todo]", out)
        self.assertIn("ENG-124 Ship it [In Progress]", out)
        self.assertNotIn("tok123", out)

    def test_list_issues_limit_clamped(self):
        self._connected()
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, {"data": {"issues": {"nodes": []}}})) as post:
            self.conn.list_issues(limit=1000)
        query = post.call_args.kwargs["json"]["query"]
        self.assertIn("first: 100", query)
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, {"data": {"issues": {"nodes": []}}})) as post:
            self.conn.list_issues(limit=0)
        self.assertIn("first: 1", post.call_args.kwargs["json"]["query"])

    def test_list_issues_empty(self):
        self._connected()
        payload = {"data": {"issues": {"nodes": []}}}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)):
            out = self.conn.list_issues()
        self.assertEqual(out, "No issues found.")

    def test_list_issues_sends_raw_key_header(self):
        self._connected()
        payload = {"data": {"issues": {"nodes": []}}}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)) as post:
            self.conn.list_issues()
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "tok123")
        self.assertNotIn("Bearer", headers["Authorization"])

    # -- create_issue ----------------------------------------------------

    def test_create_issue_format(self):
        self._connected()
        payload = {"data": {"issueCreate": {"success": True, "issue": {
            "identifier": "ENG-42",
            "title": "New thing",
            "url": "https://linear.app/team/issue/ENG-42",
        }}}}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)) as post:
            out = self.conn.create_issue("team-uuid", "New thing", "Details here")
        self.assertEqual(out, "ENG-42 https://linear.app/team/issue/ENG-42")
        sent = post.call_args.kwargs["json"]
        self.assertIn("issueCreate", sent["query"])
        self.assertEqual(sent["variables"]["input"]["teamId"], "team-uuid")
        self.assertEqual(sent["variables"]["input"]["title"], "New thing")
        self.assertEqual(sent["variables"]["input"]["description"], "Details here")

    def test_create_issue_default_description(self):
        self._connected()
        payload = {"data": {"issueCreate": {"success": True, "issue": {
            "identifier": "ENG-43", "title": "T", "url": "https://linear.app/x/ENG-43"}}}}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)) as post:
            self.conn.create_issue("team-uuid", "T")
        self.assertEqual(post.call_args.kwargs["json"]["variables"]["input"]["description"], "")

    def test_create_issue_failure_raises(self):
        self._connected()
        payload = {"data": {"issueCreate": {"success": False, "issue": None}}}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_issue("team-uuid", "T")
        self.assertIn("ERROR:", str(ctx.exception))

    # -- error paths -----------------------------------------------------

    def test_api_request_exception_raises(self):
        self._connected()
        with mock.patch("zeline.connectors.linear.requests.post",
                        side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_issues()
        self.assertIn("ERROR:", str(ctx.exception))

    def test_api_http_error_raises(self):
        self._connected()
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(500, {})):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_issues()
        self.assertIn("ERROR:", str(ctx.exception))
        self.assertIn("500", str(ctx.exception))

    def test_api_graphql_errors_raise(self):
        self._connected()
        payload = {"errors": [{"message": "Field 'nope' doesn't exist"}]}
        with mock.patch("zeline.connectors.linear.requests.post",
                        return_value=_FakeResp(200, payload)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_issues()
        self.assertIn("ERROR: Linear GraphQL error:", str(ctx.exception))
        self.assertIn("doesn't exist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
