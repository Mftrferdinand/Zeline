"""Tests for the PagerDuty connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.pagerduty import PagerDutyConnector


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

    store.save("pagerduty", {"token": "TOK", "user": "Aes Dev"})


class PagerDutyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pagerduty-test-"))
        _patch_store(self, self.tmp)
        self.conn = PagerDutyConnector()

    def test_connect_bad_token_errors_and_saves_nothing(self):
        fake = FakeResponse({"error": {"code": 2000, "message": "Invalid token"}}, status=401)
        with mock.patch("requests.get", return_value=fake) as get, \
                mock.patch("zeline.connectors.store.save") as save:
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.pagerduty.com/users/me")
        self.assertEqual(kwargs["headers"], {
            "Authorization": "Token token=BAD",
            "Accept": "application/vnd.pagerduty+json;2",
        })
        self.assertFalse(self.conn.status()["connected"])

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token=" ").startswith("ERROR:"))

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"user": {"name": "Aes Dev", "email": "aes@example.com"}})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="TOK")
        self.assertEqual(result, "Connected to PagerDuty as Aes Dev.")
        saved = store.load("pagerduty")
        self.assertEqual(saved["token"], "TOK")
        self.assertEqual(saved["user"], "Aes Dev")

    def test_connect_unreachable(self):
        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            result = self.conn.connect(token="TOK")
        self.assertTrue(result.startswith("ERROR:"))


class PagerDutyStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pagerduty-test-"))
        _patch_store(self, self.tmp)
        self.conn = PagerDutyConnector()

    def test_status_disconnected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "Aes Dev")
        self.assertNotIn("TOK", repr(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "PagerDuty disconnected.")
        self.assertEqual(self.conn.disconnect(), "PagerDuty was not connected.")


class PagerDutyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pagerduty-test-"))
        _patch_store(self, self.tmp)
        self.conn = PagerDutyConnector()
        _seed_connected()

    def test_list_incidents(self):
        payload = {"incidents": [
            {"incident_number": 42, "title": "DB down", "urgency": "high"},
            {"incident_number": 41, "title": "Latency spike", "urgency": "low"},
        ]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            out = self.conn.list_incidents(limit=2)
        self.assertEqual(out, "#42 DB down [high]\n#41 Latency spike [low]")
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://api.pagerduty.com/incidents")
        self.assertEqual(kwargs["params"], {"limit": 2, "statuses[]": "triggered"})
        self.assertEqual(kwargs["headers"]["Authorization"], "Token token=TOK")
        self.assertEqual(kwargs["headers"]["Accept"], "application/vnd.pagerduty+json;2")

    def test_list_incidents_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"incidents": []})) as req:
            self.conn.list_incidents(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)

    def test_list_incidents_custom_status(self):
        with mock.patch("requests.request", return_value=FakeResponse({"incidents": []})) as req:
            self.conn.list_incidents(status="acknowledged")
        self.assertEqual(req.call_args[1]["params"]["statuses[]"], "acknowledged")

    def test_list_incidents_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"incidents": []})):
            out = self.conn.list_incidents()
        self.assertEqual(out, "No triggered incidents.")

    def test_api_error_raises(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_incidents()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_api_not_connected_raises(self):
        from zeline.connectors import store

        store.delete("pagerduty")
        with self.assertRaises(RuntimeError):
            self.conn.list_incidents()


if __name__ == "__main__":
    unittest.main()
