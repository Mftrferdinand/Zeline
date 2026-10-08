"""Tests for the Airtable connector. All HTTP is mocked; no real network."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import airtable as airtable_mod
from zeline.connectors.airtable import AirtableConnector


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("airtable", {"token": "sekret-token-abc123"})


class AirtableConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-at-test-"))
        _patch_store(self, self.tmp)
        self.conn = AirtableConnector()

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect(token="").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="   ").startswith("ERROR:"))
        self.assertIsNone(store.load("airtable"))

    def test_connect_401_errors_and_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "INVALID_AUTH"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="bad-token")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("airtable"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="tok")
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("airtable"))

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        fake = FakeResponse({"id": "usrABC"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="good-token")
        self.assertEqual(result, "Connected to Airtable.")
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/meta/whoami"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer good-token")
        saved = store.load("airtable")
        self.assertEqual(saved["token"], "good-token")
        self.assertEqual(len(saved), 1)  # nothing but the token stored

    def test_status_not_linked(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_linked_never_leaks_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "linked")
        self.assertNotIn("sekret-token-abc123", repr(status))
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Airtable disconnected.")
        self.assertEqual(self.conn.disconnect(), "Airtable was not connected.")


class AirtableOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-at-test-"))
        _patch_store(self, self.tmp)
        self.conn = AirtableConnector()
        _seed_connected()

    def test_list_records_formats_fields(self):
        payload = {
            "records": [
                {"id": "recAAA", "fields": {"Name": "Acme", "Status": "active"}},
                {"id": "recBBB", "fields": {}},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_records("appX", "tblY", limit=10)
        self.assertEqual(
            result, "recAAA: Name=Acme, Status=active\nrecBBB: (no fields)"
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/appX/tblY"))
        self.assertEqual(kwargs["params"], {"maxRecords": 10})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sekret-token-abc123")

    def test_list_records_clamps_limit(self):
        with mock.patch("requests.request", return_value=FakeResponse({"records": []})) as req:
            self.conn.list_records("appX", "tblY", limit=500)
            self.assertEqual(req.call_args[1]["params"], {"maxRecords": 100})
            self.conn.list_records("appX", "tblY", limit=0)
            self.assertEqual(req.call_args[1]["params"], {"maxRecords": 1})

    def test_list_records_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"records": []})):
            self.assertEqual(self.conn.list_records("appX", "tblY"), "No records found.")

    def test_list_records_missing_ids(self):
        self.assertTrue(self.conn.list_records("", "tblY").startswith("ERROR:"))
        self.assertTrue(self.conn.list_records("appX", "").startswith("ERROR:"))

    def test_create_record(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse({"id": "recNEW"})
        ) as req:
            result = self.conn.create_record("appX", "tblY", {"Name": "Baru"})
        self.assertEqual(result, "Record dibuat: recNEW")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/appX/tblY"))
        self.assertEqual(kwargs["json"], {"fields": {"Name": "Baru"}})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sekret-token-abc123")

    def test_create_record_bad_fields(self):
        self.assertTrue(self.conn.create_record("appX", "tblY", {}).startswith("ERROR:"))
        self.assertTrue(
            self.conn.create_record("appX", "tblY", "notadict").startswith("ERROR:")
        )
        self.assertTrue(self.conn.create_record("", "tblY", {"a": 1}).startswith("ERROR:"))

    def test_api_http_error_raises(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse({"error": "x"}, status=403)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_records("appX", "tblY")
        self.assertTrue(str(ctx.exception).startswith("ERROR: Airtable API 403"))

    def test_api_network_error_raises(self):
        with mock.patch(
            "requests.request", side_effect=requests.Timeout("slow")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_records("appX", "tblY")
        self.assertIn("request failed", str(ctx.exception))

    def test_operation_without_connection_raises(self):
        from zeline.connectors import store

        store.delete("airtable")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_records("appX", "tblY")
        self.assertTrue(str(ctx.exception).startswith("ERROR: Airtable not connected"))
        with self.assertRaises(RuntimeError):
            self.conn.create_record("appX", "tblY", {"a": 1})


class AirtableSecretHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-at-test-"))
        _patch_store(self, self.tmp)
        self.conn = AirtableConnector()

    def test_error_messages_never_contain_token(self):
        fake = FakeResponse({"error": "bad"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="my-super-secret")
        self.assertNotIn("my-super-secret", result)
        _seed_connected()
        with mock.patch(
            "requests.request", return_value=FakeResponse({"error": "x"}, status=500)
        ):
            try:
                self.conn.list_records("appX", "tblY")
            except RuntimeError as exc:
                self.assertNotIn("sekret-token-abc123", str(exc))

    def test_registration(self):
        from zeline.connectors import get

        conn = get("airtable")
        self.assertIsInstance(conn, AirtableConnector)
        self.assertEqual(conn.auth_kind, "pat")
        self.assertEqual(conn.name, "Airtable")


if __name__ == "__main__":
    unittest.main()
