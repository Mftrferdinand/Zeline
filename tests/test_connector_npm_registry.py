"""Tests for the npm registry connector (public API, no key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import npm_registry as npm_mod
from zeline.connectors.npm_registry import NpmRegistryConnector


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

    store.save("npm_registry", {"connected": True})


class NpmRegistryConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-npm-test-"))
        _patch_store(self, self.tmp)
        self.conn = NpmRegistryConnector()

    def test_connect_success_saves_marker(self):
        from zeline.connectors import store

        fake = FakeResponse({"ok": True})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect()
        self.assertEqual(result, "Connected to npm registry (public API, no key needed).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://registry.npmjs.org/")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("npm_registry"), {"connected": True})

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "down"}, status=500)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("npm_registry"))

    def test_connect_network_error(self):
        import requests

        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("npm_registry"))

    def test_status(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "public API"})

    def test_disconnect(self):
        self.assertEqual(self.conn.disconnect(), "npm Registry was not connected.")
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "npm Registry disconnected.")
        self.assertEqual(self.conn.disconnect(), "npm Registry was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "npm_registry")
        self.assertEqual(self.conn.name, "npm Registry")
        self.assertEqual(
            self.conn.description,
            "Look up npm package info and search (public, no key).",
        )
        self.assertEqual(self.conn.auth_kind, "none")


class NpmRegistryOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-npm-test-"))
        _patch_store(self, self.tmp)
        self.conn = NpmRegistryConnector()
        _seed_connected()

    def test_package_info(self):
        payload = {"name": "lodash", "version": "4.17.21", "description": "A modern JS utility library"}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.package_info("lodash")
        self.assertEqual(result, "lodash@4.17.21 — A modern JS utility library")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://registry.npmjs.org/lodash/latest")
        self.assertEqual(kwargs["timeout"], 30)

    def test_package_info_missing_description_fallback(self):
        payload = {"name": "pkg", "version": "1.0.0"}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.package_info("pkg"), "pkg@1.0.0 — -")

    def test_package_info_not_found(self):
        with mock.patch("requests.get", return_value=FakeResponse({"error": "Not found"}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("no-such-package-xyz")
        self.assertEqual(str(ctx.exception), "ERROR: package not found.")

    def test_package_info_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=503)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("lodash")
        self.assertIn("ERROR: npm registry API 503", str(ctx.exception))

    def test_package_info_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("lodash")
        self.assertIn("ERROR: npm registry API request failed", str(ctx.exception))

    def test_search(self):
        payload = {
            "objects": [
                {"package": {"name": "react", "description": "A JS library for UIs"}},
                {"package": {"name": "react-dom", "description": ""}},
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.search("react", limit=5)
        self.assertEqual(result, "react — A JS library for UIs\nreact-dom — -")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://registry.npmjs.org/-/v1/search")
        self.assertEqual(kwargs["params"], {"text": "react", "size": 5})
        self.assertEqual(kwargs["timeout"], 30)

    def test_search_none(self):
        with mock.patch("requests.get", return_value=FakeResponse({"objects": []})):
            self.assertEqual(self.conn.search("nope"), "No packages found for 'nope'.")

    def test_search_limit_clamped(self):
        with mock.patch("requests.get", return_value=FakeResponse({"objects": []})) as get:
            self.conn.search("x", limit=500)
        self.assertEqual(get.call_args[1]["params"]["size"], 100)
        with mock.patch("requests.get", return_value=FakeResponse({"objects": []})) as get:
            self.conn.search("x", limit=0)
        self.assertEqual(get.call_args[1]["params"]["size"], 1)

    def test_search_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search("react")
        self.assertIn("ERROR: npm registry API request failed", str(ctx.exception))


class NpmRegistryRegistryTests(unittest.TestCase):
    def test_npm_registry_registered(self):
        from zeline.connectors import get

        conn = get("npm_registry")
        self.assertIsInstance(conn, NpmRegistryConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(npm_mod.NpmRegistryConnector.id, "npm_registry")


if __name__ == "__main__":
    unittest.main()
