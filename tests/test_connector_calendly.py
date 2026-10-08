"""Tests for the Calendly connector. All HTTP is mocked; no real network or tokens."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.calendly import CalendlyConnector

USER_URI = "https://api.calendly.com/users/ABC123"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path, with_uri: bool = True):
    from zeline.connectors import store

    data = {"token": "cal-secret-token"}
    if with_uri:
        data["user_uri"] = USER_URI
    store.save("calendly", data)


class CalendlyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-calendly-test-"))
        _patch_store(self, self.tmp)
        self.conn = CalendlyConnector()

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            with mock.patch("zeline.connectors.store.save") as save:
                self.assertTrue(self.conn.connect(token=bad).startswith("ERROR:"))
                save.assert_not_called()
        self.assertIsNone(store.load("calendly"))

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"title": "Unauthorized", "detail": "Invalid auth"}, status=401)
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="cal-bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/users/me"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer cal-bad")
        self.assertIsNone(store.load("calendly"))

    def test_connect_network_error(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="cal-abc")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("could not reach", result)
        self.assertIsNone(store.load("calendly"))

    def test_connect_success_saves_token_and_user_uri(self):
        fake = FakeResponse(
            {"resource": {"uri": USER_URI, "name": "Jane", "email": "jane@example.com"}}
        )
        with mock.patch("requests.get", return_value=fake), mock.patch(
            "zeline.connectors.store.save"
        ) as save:
            result = self.conn.connect(token="cal-secret-token")
        self.assertEqual(result, "Connected to Calendly as Jane.")
        self.assertNotIn("cal-secret-token", result)
        save.assert_called_once_with(
            "calendly", {"token": "cal-secret-token", "user_uri": USER_URI}
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_never_leaks_token(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("cal-secret-token", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Calendly disconnected.")
        self.assertEqual(self.conn.disconnect(), "Calendly was not connected.")


class CalendlyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-calendly-test-"))
        _patch_store(self, self.tmp)
        self.conn = CalendlyConnector()
        _seed_connected(self.tmp)

    def test_list_events(self):
        fake = FakeResponse(
            {
                "collection": [
                    {"name": "30 Minute Meeting", "start_time": "2026-10-09T06:00:00Z"},
                    {"name": "Intro Call", "start_time": "2026-10-10T08:00:00Z"},
                ]
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_events(limit=10)
        self.assertEqual(
            result,
            "30 Minute Meeting (2026-10-09T06:00:00Z)\nIntro Call (2026-10-10T08:00:00Z)",
        )
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/scheduled_events"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer cal-secret-token")
        self.assertEqual(kwargs["params"], {"user": USER_URI, "count": 10})

    def test_list_events_limit_clamped(self):
        fake = FakeResponse({"collection": []})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.list_events(limit=500)
            self.assertEqual(req.call_args[1]["params"]["count"], 100)
            self.conn.list_events(limit=0)
            self.assertEqual(req.call_args[1]["params"]["count"], 1)

    def test_list_events_empty(self):
        fake = FakeResponse({"collection": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_events(), "No scheduled events found.")

    def test_list_events_missing_user_uri(self):
        _seed_connected(self.tmp, with_uri=False)
        fake = FakeResponse({"collection": []})
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"user URI is missing"):
                self.conn.list_events()


class CalendlyErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-calendly-test-"))
        _patch_store(self, self.tmp)
        self.conn = CalendlyConnector()
        _seed_connected(self.tmp)

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Calendly API request failed"):
                self.conn.list_events()

    def test_operation_http_403(self):
        fake = FakeResponse({"title": "Forbidden"}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Calendly API 403"):
                self.conn.list_events()

    def test_operation_without_connect(self):
        from zeline.connectors import store

        store.delete("calendly")
        with self.assertRaisesRegex(RuntimeError, r"connect calendly"):
            self.conn.list_events()


if __name__ == "__main__":
    unittest.main()
