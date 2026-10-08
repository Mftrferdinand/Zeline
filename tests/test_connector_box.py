"""Tests for the Box connector. All HTTP is mocked; no real network or keys."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import box as box_mod
from zeline.connectors.box import BoxConnector

API_BASE = "https://api.box.com/2.0"


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


def _seed_connected():
    from zeline.connectors import store

    store.save("box", {"access_token": "box_token-test", "login": "user@example.com"})


class BoxConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-box-test-"))
        _patch_store(self, self.tmp)
        self.conn = BoxConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"login": "user@example.com"})
        ) as get:
            result = self.conn.connect(access_token="box_token-test")
        self.assertEqual(result, "Connected to Box as user@example.com.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/users/me")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer box_token-test"})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("box"), {"access_token": "box_token-test", "login": "user@example.com"}
        )

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            result = self.conn.connect(access_token=bad)
            self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("box"))

    def test_connect_network_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(access_token="box_token-test")
        self.assertTrue(result.startswith("ERROR: could not reach Box"))
        self.assertIsNone(store.load("box"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(access_token="bad")
        self.assertTrue(result.startswith("ERROR: Box rejected the access token"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("box"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "user@example.com"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Box disconnected.")
        self.assertEqual(self.conn.disconnect(), "Box was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "box")
        self.assertEqual(self.conn.name, "Box")
        self.assertEqual(self.conn.auth_kind, "pat")


class BoxOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-box-test-"))
        _patch_store(self, self.tmp)
        self.conn = BoxConnector()
        _seed_connected()

    def test_list_files(self):
        payload = {
            "entries": [
                {"type": "folder", "id": "111", "name": "Documents"},
                {"type": "file", "id": "222", "name": "report.pdf", "size": 1234},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_files(folder_id="0", limit=2)
        self.assertEqual(result, "[folder] Documents\n[file] report.pdf (1234 bytes)")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/folders/0/items")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_files_subfolder(self):
        payload = {"entries": [{"type": "file", "id": "333", "name": "notes.txt", "size": 77}]}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_files(folder_id="999", limit=1)
        self.assertEqual(result, "[file] notes.txt (77 bytes)")
        self.assertEqual(get.call_args.args[0], f"{API_BASE}/folders/999/items")

    def test_list_files_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"entries": []})):
            self.assertEqual(self.conn.list_files(), "No items in folder 0.")

    def test_list_files_limit_clamped(self):
        payload = {"entries": [
            {"type": "file", "id": f"{i}", "name": f"f{i}.txt", "size": i} for i in range(150)
        ]}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.list_files(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 100})

    def test_list_files_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_files()
        self.assertIn("ERROR: Box API 404 on /folders/0/items.", str(ctx.exception))

    def test_list_files_network_error(self):
        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_files()
        self.assertIn("ERROR: Box API request failed", str(ctx.exception))

    def test_get_file_info(self):
        payload = {
            "type": "file", "id": "222", "name": "report.pdf",
            "size": 1234, "modified_at": "2026-10-07T10:00:00-07:00",
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.get_file_info("222")
        self.assertEqual(result, "report.pdf: 1234 bytes, 2026-10-07T10:00:00-07:00")
        self.assertEqual(get.call_args.args[0], f"{API_BASE}/files/222")
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    def test_get_file_info_unexpected_body(self):
        with mock.patch("requests.get", return_value=FakeResponse([1, 2, 3])):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_file_info("222")
        self.assertEqual(str(ctx.exception), "ERROR: Box returned an unexpected file response.")

    def test_get_file_info_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_file_info("222")
        self.assertIn("ERROR: Box API 500 on /files/222.", str(ctx.exception))

    def test_operations_disconnected_raise(self):
        from zeline.connectors import store

        store.delete("box")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_files()
        self.assertIn("zeline connect box", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_file_info("222")


class BoxRegistryTests(unittest.TestCase):
    def test_box_registered(self):
        from zeline.connectors import get

        conn = get("box")
        self.assertIsInstance(conn, BoxConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(box_mod.BoxConnector.id, "box")


if __name__ == "__main__":
    unittest.main()
