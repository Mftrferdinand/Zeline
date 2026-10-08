"""Tests for the RubyGems connector (public, no key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import rubygems as rubygems_mod
from zeline.connectors.rubygems import RubyGemsConnector


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

    store.save("rubygems", {"connected": True})


class RubyGemsConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-rubygems-test-"))
        _patch_store(self, self.tmp)
        self.conn = RubyGemsConnector()

    def test_connect_success_saves_marker(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({})) as get:
            result = self.conn.connect()
        self.assertEqual(result, "Connected to RubyGems (public API, no key needed).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://rubygems.org/")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("rubygems")
        self.assertEqual(saved, {"connected": True})

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=503)):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("rubygems"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "public API, no key needed")

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "RubyGems disconnected.")
        self.assertEqual(self.conn.disconnect(), "RubyGems was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "rubygems")
        self.assertEqual(self.conn.name, "RubyGems")
        self.assertEqual(self.conn.auth_kind, "none")


class RubyGemsOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-rubygems-test-"))
        _patch_store(self, self.tmp)
        self.conn = RubyGemsConnector()

    def test_package_info(self):
        payload = {"version": "8.1.1", "info": "Make the web work"}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.package_info("rails")
        self.assertEqual(result, "rails 8.1.1 — Make the web work")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://rubygems.org/api/v1/gems/rails.json")
        self.assertEqual(kwargs["timeout"], 30)

    def test_package_info_missing_fields(self):
        with mock.patch("requests.get", return_value=FakeResponse({})):
            self.assertEqual(self.conn.package_info("nogem"), "nogem - — -")

    def test_package_info_not_found(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("no-such-gem-xyz")
        self.assertEqual(str(ctx.exception), "ERROR: gem not found.")

    def test_package_info_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("rails")
        self.assertIn("ERROR: RubyGems API 500", str(ctx.exception))

    def test_package_info_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("rails")
        self.assertIn("ERROR: RubyGems API request failed", str(ctx.exception))

    def test_search(self):
        payload = [
            {"name": "rails", "version": "8.1.1"},
            {"name": "rails-api", "version": "0.4.1"},
        ]
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.search("rails", limit=2)
        self.assertEqual(result, "rails 8.1.1\nrails-api 0.4.1")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://rubygems.org/api/v1/search.json")
        self.assertEqual(kwargs["params"], {"query": "rails"})

    def test_search_dict_response(self):
        with mock.patch("requests.get", return_value=FakeResponse({"name": "rails", "version": "8.1.1"})):
            self.assertEqual(self.conn.search("rails"), "rails 8.1.1")

    def test_search_none(self):
        with mock.patch("requests.get", return_value=FakeResponse([])):
            self.assertEqual(self.conn.search("no-such-thing"), "No gems found for 'no-such-thing'.")

    def test_search_limit_clamped(self):
        payload = [{"name": f"g{i}", "version": "1.0"} for i in range(5)]
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            self.assertEqual(len(self.conn.search("x", limit=500).splitlines()), 5)
            self.assertEqual(len(self.conn.search("x", limit=0).splitlines()), 1)


class RubyGemsRegistryTests(unittest.TestCase):
    def test_rubygems_registered(self):
        from zeline.connectors import get

        conn = get("rubygems")
        self.assertIsInstance(conn, RubyGemsConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(rubygems_mod.RubyGemsConnector.id, "rubygems")


if __name__ == "__main__":
    unittest.main()
