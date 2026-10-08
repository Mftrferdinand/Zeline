"""Tests for the Dropbox connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.dropbox import DropboxConnector


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


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("dropbox", {"token": "SECRET-TOKEN", "user": "Acme Ops"})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-dropbox-test-"))
        _patch_store(self, self.tmp)
        self.conn = DropboxConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"name": {"display_name": "Acme Ops"}})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.connect(token="SECRET-TOKEN")
        self.assertEqual(result, "Connected to Dropbox as Acme Ops.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.dropboxapi.com/2/users/get_current_account")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        saved = store.load("dropbox")
        self.assertEqual(saved["token"], "SECRET-TOKEN")
        self.assertEqual(saved["user"], "Acme Ops")

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("dropbox"))

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="T")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-TOKEN", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Dropbox disconnected.")
        self.assertEqual(self.conn.disconnect(), "Dropbox was not connected.")


class ListFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-dropbox-test-"))
        _patch_store(self, self.tmp)
        self.conn = DropboxConnector()
        _seed_connected(self.tmp)

    def _entries(self):
        return {
            "entries": [
                {".tag": "file", "name": "report.pdf"},
                {".tag": "folder", "name": "photos"},
            ]
        }

    def test_list_files_formats(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._entries())) as req:
            result = self.conn.list_files()
        self.assertEqual(result, "report.pdf (file)\nphotos (folder)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.dropboxapi.com/2/files/list_folder")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        self.assertEqual(kwargs["json"], {"path": "", "limit": 10})

    def test_list_files_with_path(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._entries())) as req:
            self.conn.list_files(path="/docs")
        self.assertEqual(req.call_args[1]["json"]["path"], "/docs")

    def test_list_files_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._entries())) as req:
            self.conn.list_files(limit=500)
        self.assertEqual(req.call_args[1]["json"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse(self._entries())) as req:
            self.conn.list_files(limit=0)
        self.assertEqual(req.call_args[1]["json"]["limit"], 1)

    def test_list_files_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"entries": []})):
            self.assertEqual(self.conn.list_files(), "No files found.")

    def test_list_files_api_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=409)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_files()
        self.assertIn("ERROR: Dropbox API 409", str(ctx.exception))

    def test_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("dropbox")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_files()
        self.assertIn("zeline connect dropbox", str(ctx.exception))


class GetMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-dropbox-test-"))
        _patch_store(self, self.tmp)
        self.conn = DropboxConnector()
        _seed_connected(self.tmp)

    def test_get_metadata_formats(self):
        fake = FakeResponse(
            {
                "name": "report.pdf",
                "size": 12345,
                "client_modified": "2026-10-01T10:00:00Z",
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.get_metadata("/report.pdf")
        self.assertEqual(result, "report.pdf | 12345 | 2026-10-01T10:00:00Z")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api.dropboxapi.com/2/files/get_metadata")
        self.assertEqual(kwargs["json"], {"path": "/report.pdf"})

    def test_get_metadata_empty_path(self):
        self.assertTrue(self.conn.get_metadata("").startswith("ERROR:"))
        self.assertTrue(self.conn.get_metadata("   ").startswith("ERROR:"))

    def test_get_metadata_api_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=409)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_metadata("/missing")
        self.assertIn("ERROR: Dropbox API 409", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
