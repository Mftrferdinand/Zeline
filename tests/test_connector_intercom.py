"""Tests for the Intercom connector. All HTTP is mocked; no real network or tokens."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.intercom import IntercomConnector


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


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("intercom", {"token": "ic-secret-token"})


class IntercomConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-intercom-test-"))
        _patch_store(self, self.tmp)
        self.conn = IntercomConnector()

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            with mock.patch("zeline.connectors.store.save") as save:
                self.assertTrue(self.conn.connect(token=bad).startswith("ERROR:"))
                save.assert_not_called()
        self.assertIsNone(store.load("intercom"))

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"errors": [{"code": "unauthorized"}]}, status=401)
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="ic-bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/me"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ic-bad")
        self.assertEqual(kwargs["headers"]["Intercom-Version"], "2.11")
        self.assertIsNone(store.load("intercom"))

    def test_connect_network_error(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="ic-abc")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("could not reach", result)
        self.assertIsNone(store.load("intercom"))

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        fake = FakeResponse({"type": "admin", "id": "123"})
        with mock.patch("requests.get", return_value=fake), mock.patch(
            "zeline.connectors.store.save"
        ) as save:
            result = self.conn.connect(token="ic-secret-token")
        self.assertEqual(result, "Connected to Intercom.")
        self.assertNotIn("ic-secret-token", result)
        save.assert_called_once_with("intercom", {"token": "ic-secret-token"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_never_leaks_token(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("ic-secret-token", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Intercom disconnected.")
        self.assertEqual(self.conn.disconnect(), "Intercom was not connected.")


class IntercomOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-intercom-test-"))
        _patch_store(self, self.tmp)
        self.conn = IntercomConnector()
        _seed_connected(self.tmp)

    def test_list_conversations(self):
        fake = FakeResponse(
            {
                "conversations": [
                    {"id": "conv1", "title": "Billing question", "state": "open"},
                    {"id": "conv2", "title": "Bug report", "state": "closed"},
                ]
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_conversations(limit=10)
        self.assertEqual(
            result, "conv1: Billing question [open]\nconv2: Bug report [closed]"
        )
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/conversations"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer ic-secret-token")
        self.assertEqual(kwargs["headers"]["Intercom-Version"], "2.11")
        self.assertEqual(kwargs["params"], {"per_page": 10})

    def test_list_conversations_limit_clamped(self):
        fake = FakeResponse({"conversations": []})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.list_conversations(limit=500)
            self.assertEqual(req.call_args[1]["params"]["per_page"], 100)
            self.conn.list_conversations(limit=0)
            self.assertEqual(req.call_args[1]["params"]["per_page"], 1)

    def test_list_conversations_empty(self):
        fake = FakeResponse({"conversations": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_conversations(), "No conversations found.")


class IntercomErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-intercom-test-"))
        _patch_store(self, self.tmp)
        self.conn = IntercomConnector()
        _seed_connected(self.tmp)

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Intercom API request failed"):
                self.conn.list_conversations()

    def test_operation_http_403(self):
        fake = FakeResponse({"errors": [{"code": "forbidden"}]}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Intercom API 403"):
                self.conn.list_conversations()

    def test_operation_without_connect(self):
        from zeline.connectors import store

        store.delete("intercom")
        with self.assertRaisesRegex(RuntimeError, r"not connected"):
            self.conn.list_conversations()


if __name__ == "__main__":
    unittest.main()
