"""Tests for the Sentry connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.sentry import SentryConnector


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("sentry", {"token": "TOK", "organization_slug": "acme", "org_name": "Acme"})


class SentryConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sentry-test-"))
        _patch_store(self, self.tmp)
        self.conn = SentryConnector()

    def test_connect_bad_token_errors_and_saves_nothing(self):
        fake = FakeResponse({"detail": "Invalid token."}, status=401)
        with mock.patch("requests.get", return_value=fake) as get, \
                mock.patch("zeline.connectors.store.save") as save:
            result = self.conn.connect(token="BAD", organization_slug="acme")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://sentry.io/api/0/organizations/acme/")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer BAD"})
        self.assertFalse(self.conn.status()["connected"])

    def test_connect_missing_params(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="TOK").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(organization_slug="acme").startswith("ERROR:"))

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"slug": "acme", "name": "Acme"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="TOK", organization_slug="acme")
        self.assertEqual(result, "Connected to Sentry organization acme.")
        saved = store.load("sentry")
        self.assertEqual(saved["token"], "TOK")
        self.assertEqual(saved["organization_slug"], "acme")

    def test_connect_unreachable(self):
        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            result = self.conn.connect(token="TOK", organization_slug="acme")
        self.assertTrue(result.startswith("ERROR:"))


class SentryStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sentry-test-"))
        _patch_store(self, self.tmp)
        self.conn = SentryConnector()

    def test_status_disconnected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "org acme")
        self.assertNotIn("TOK", repr(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Sentry disconnected.")
        self.assertEqual(self.conn.disconnect(), "Sentry was not connected.")


class SentryOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sentry-test-"))
        _patch_store(self, self.tmp)
        self.conn = SentryConnector()
        _seed_connected()

    def test_list_issues(self):
        payload = [
            {"shortId": "WEB-1", "title": "TypeError: x is undefined", "level": "error"},
            {"shortId": "WEB-2", "title": "Slow query", "level": "warning"},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            out = self.conn.list_issues(limit=2)
        self.assertEqual(
            out, "WEB-1: TypeError: x is undefined [error]\nWEB-2: Slow query [warning]"
        )
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://sentry.io/api/0/organizations/acme/issues/")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer TOK")

    def test_list_issues_with_project_slug(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_issues(project_slug="web")
        self.assertEqual(req.call_args[1]["params"], {"limit": 10, "project": "web"})

    def test_list_issues_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_issues(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)

    def test_list_issues_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            out = self.conn.list_issues()
        self.assertEqual(out, "No issues found.")

    def test_api_error_raises(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_issues()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_api_not_connected_raises(self):
        from zeline.connectors import store

        store.delete("sentry")
        with self.assertRaises(RuntimeError):
            self.conn.list_issues()


if __name__ == "__main__":
    unittest.main()
