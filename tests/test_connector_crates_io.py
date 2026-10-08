"""Tests for the crates.io connector (public API). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import crates_io as crates_io_mod
from zeline.connectors.crates_io import CratesIoConnector

API_BASE = "https://crates.io/api/v1"
USER_AGENT = "zeline-connector (https://github.com/Zerolinear)"


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

    store.save("crates_io", {"connected": True})


def _get_side_effect(mapping):
    def _side_effect(url, *args, **kwargs):
        if url in mapping:
            payload, status = mapping[url]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected GET {url}")

    return _side_effect


def _assert_user_agent(call):
    headers = call.kwargs.get("headers", {})
    assert headers.get("User-Agent") == USER_AGENT, (
        f"User-Agent header missing/wrong: {headers!r}"
    )


class CratesIoConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cratesio-test-"))
        _patch_store(self, self.tmp)
        self.conn = CratesIoConnector()

    def test_connect_success_saves_marker(self):
        from zeline.connectors import store

        payload = {"num_downloads": 123, "num_crates": 456}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.connect()
        self.assertEqual(result, "Connected to crates.io (public API, no key needed).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/summary")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["User-Agent"], USER_AGENT)
        self.assertEqual(store.load("crates_io"), {"connected": True})

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach crates.io API"))
        self.assertIsNone(store.load("crates_io"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach crates.io API"))
        self.assertIn("500", result)
        self.assertIsNone(store.load("crates_io"))

    def test_connect_unexpected_body_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse([1, 2, 3])):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("crates_io"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "public API"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_carries_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertNotIn("secret", status)
        self.assertNotIn("token", status)

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "crates.io disconnected.")
        self.assertEqual(self.conn.disconnect(), "crates.io was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "crates_io")
        self.assertEqual(self.conn.name, "crates.io")
        self.assertEqual(
            self.conn.description,
            "Look up Rust crates and search crates.io (public API, no key needed).",
        )
        self.assertEqual(self.conn.auth_kind, "none")


class CratesIoOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cratesio-test-"))
        _patch_store(self, self.tmp)
        self.conn = CratesIoConnector()
        _seed_connected()

    def test_crate_info(self):
        payload = {
            "crate": {
                "id": "serde",
                "description": "A generic serialization/deserialization framework",
                "max_version": "1.0.219",
                "downloads": 987654321,
            }
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.crate_info("serde")
        self.assertIn("Name: serde", result)
        self.assertIn("Description: A generic serialization/deserialization framework", result)
        self.assertIn("Max version: 1.0.219", result)
        self.assertIn("Downloads: 987654321", result)
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/crates/serde")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["User-Agent"], USER_AGENT)

    def test_crate_info_not_found_404(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.crate_info("no-such-crate-xyz")
        self.assertEqual(str(ctx.exception), "ERROR: crate not found.")

    def test_crate_info_empty_name(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.crate_info("   ")
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_crate_info_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=503)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.crate_info("serde")
        self.assertIn("ERROR: crates.io API 503.", str(ctx.exception))

    def test_crate_info_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.crate_info("serde")
        self.assertIn("ERROR: crates.io API request failed", str(ctx.exception))

    def test_search_crates(self):
        payload = {
            "crates": [
                {
                    "id": "serde",
                    "max_version": "1.0.219",
                    "description": "A generic serialization/deserialization framework",
                },
                {"id": "tokio", "max_version": "1.47.0", "description": ""},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.search_crates("json", limit=2)
        self.assertIn(
            "serde (1.0.219) — A generic serialization/deserialization framework",
            result,
        )
        self.assertIn("tokio (1.47.0)", result)
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/crates")
        self.assertEqual(kwargs["params"], {"q": "json", "per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["User-Agent"], USER_AGENT)

    def test_search_crates_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"crates": []})):
            result = self.conn.search_crates("zzz-no-match")
        self.assertEqual(result, 'No crates found for "zzz-no-match".')

    def test_search_crates_limit_clamped(self):
        payload = {
            "crates": [
                {"id": f"crate-{i}", "max_version": "1.0.0", "description": "d"}
                for i in range(100)
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            self.conn.search_crates("json", limit=500)
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 100)

        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            self.conn.search_crates("json", limit=0)
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 1)

    def test_search_crates_empty_query(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.search_crates("   ")
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_search_crates_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_crates("json")
        self.assertIn("ERROR: crates.io API 500.", str(ctx.exception))

    def test_search_crates_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_crates("json")
        self.assertIn("ERROR: crates.io API request failed", str(ctx.exception))

    def test_user_agent_sent_on_all_requests(self):
        payload_info = {
            "crate": {"id": "serde", "max_version": "1.0.0", "downloads": 1}
        }
        payload_search = {
            "crates": [{"id": "serde", "max_version": "1.0.0"}]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload_info)) as get:
            self.conn.crate_info("serde")
            _assert_user_agent(get.call_args)
        with mock.patch("requests.get", return_value=FakeResponse(payload_search)) as get:
            self.conn.search_crates("json")
            _assert_user_agent(get.call_args)

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("crates_io")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.crate_info("serde")
        self.assertIn("zeline connect crates_io", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search_crates("json")


class CratesIoRegistryTests(unittest.TestCase):
    def test_crates_io_registered(self):
        from zeline.connectors import get

        conn = get("crates_io")
        self.assertIsInstance(conn, CratesIoConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(crates_io_mod.CratesIoConnector.id, "crates_io")


if __name__ == "__main__":
    unittest.main()
