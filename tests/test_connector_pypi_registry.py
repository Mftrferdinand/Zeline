"""Tests for the PyPI registry connector (public API, no key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import pypi_registry as pypi_mod
from zeline.connectors.pypi_registry import PypiRegistryConnector


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

    store.save("pypi_registry", {"connected": True})


class PypiRegistryConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pypi-test-"))
        _patch_store(self, self.tmp)
        self.conn = PypiRegistryConnector()

    def test_connect_success_saves_marker(self):
        from zeline.connectors import store

        fake = FakeResponse("<html>PyPI</html>")
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect()
        self.assertEqual(result, "Connected to PyPI (public API, no key needed).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://pypi.org/")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("pypi_registry"), {"connected": True})

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse("down", status=503)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("pypi_registry"))

    def test_connect_network_error(self):
        import requests

        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("pypi_registry"))

    def test_status(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "public API"})

    def test_disconnect(self):
        self.assertEqual(self.conn.disconnect(), "PyPI was not connected.")
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "PyPI disconnected.")
        self.assertEqual(self.conn.disconnect(), "PyPI was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "pypi_registry")
        self.assertEqual(self.conn.name, "PyPI")
        self.assertEqual(
            self.conn.description,
            "Look up Python package info on PyPI (public, no key).",
        )
        self.assertEqual(self.conn.auth_kind, "none")


class PypiRegistryOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pypi-test-"))
        _patch_store(self, self.tmp)
        self.conn = PypiRegistryConnector()
        _seed_connected()

    def test_package_info(self):
        payload = {"info": {"name": "requests", "version": "2.31.0", "summary": "Python HTTP for Humans."}}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.package_info("requests")
        self.assertEqual(result, "requests 2.31.0 — Python HTTP for Humans.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://pypi.org/pypi/requests/json")
        self.assertEqual(kwargs["timeout"], 30)

    def test_package_info_missing_summary_fallback(self):
        payload = {"info": {"name": "pkg", "version": "1.0.0"}}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.package_info("pkg"), "pkg 1.0.0 — -")

    def test_package_info_not_found(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("no-such-package-xyz")
        self.assertEqual(str(ctx.exception), "ERROR: package not found.")

    def test_package_info_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("requests")
        self.assertIn("ERROR: PyPI API 500", str(ctx.exception))

    def test_package_info_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("requests")
        self.assertIn("ERROR: PyPI API request failed", str(ctx.exception))


class PypiRegistryRegistryTests(unittest.TestCase):
    def test_pypi_registry_registered(self):
        from zeline.connectors import get

        conn = get("pypi_registry")
        self.assertIsInstance(conn, PypiRegistryConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(pypi_mod.PypiRegistryConnector.id, "pypi_registry")


if __name__ == "__main__":
    unittest.main()
