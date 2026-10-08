"""Tests for the Packagist connector (public API). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import packagist as packagist_mod
from zeline.connectors.packagist import PackagistConnector

SEARCH_BASE = "https://packagist.org"
METADATA_BASE = "https://repo.packagist.org"


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

    store.save("packagist", {"connected": True})


class PackagistConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-packagist-test-"))
        _patch_store(self, self.tmp)
        self.conn = PackagistConnector()

    def test_connect_success_saves_marker(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"results": []})) as get:
            result = self.conn.connect()
        self.assertEqual(result, "Connected to Packagist (public API, no key needed).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{SEARCH_BASE}/search.json?q=zeline")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("packagist"), {"connected": True})

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach Packagist API"))
        self.assertIsNone(store.load("packagist"))

    def test_connect_http_500_save_not_called(self):
        from zeline.connectors import store

        with (
            mock.patch("requests.get", return_value=FakeResponse({}, status=500)),
            mock.patch.object(store, "save") as save_mock,
        ):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR: could not reach Packagist API"))
        self.assertIn("500", result)
        save_mock.assert_not_called()
        self.assertIsNone(store.load("packagist"))

    def test_connect_network_error_save_not_called(self):
        import requests
        from zeline.connectors import store

        with (
            mock.patch("requests.get", side_effect=requests.Timeout("slow")),
            mock.patch.object(store, "save") as save_mock,
        ):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        save_mock.assert_not_called()

    def test_connect_unexpected_body_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"oops": True})):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("packagist"))

    def test_connect_missing_results_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"total": 0})):
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("packagist"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "public API"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Packagist disconnected.")
        self.assertEqual(self.conn.disconnect(), "Packagist was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "packagist")
        self.assertEqual(self.conn.name, "Packagist")
        self.assertEqual(
            self.conn.description,
            "Look up PHP packages and search Packagist (public API, no key needed).",
        )
        self.assertEqual(self.conn.auth_kind, "none")


class PackagistOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-packagist-test-"))
        _patch_store(self, self.tmp)
        self.conn = PackagistConnector()
        _seed_connected()

    def _package_payload(self):
        return {
            "packages": {
                "laravel/framework": [
                    {
                        "name": "laravel/framework",
                        "description": "The Laravel Framework.",
                        "version": "v11.9.0",
                        "time": "2024-06-04T00:00:00+00:00",
                    },
                    {
                        "name": "laravel/framework",
                        "description": "The Laravel Framework.",
                        "version": "v11.8.0",
                        "time": "2024-05-28T00:00:00+00:00",
                    },
                ]
            }
        }

    def test_package_info(self):
        with mock.patch("requests.get", return_value=FakeResponse(self._package_payload())) as get:
            result = self.conn.package_info("laravel", "framework")
        self.assertEqual(
            result,
            "Name: laravel/framework\n"
            "Description: The Laravel Framework.\n"
            "Latest version: v11.9.0\n"
            "Released: 2024-06-04T00:00:00+00:00",
        )
        self.assertEqual(
            get.call_args.args[0],
            f"{METADATA_BASE}/p2/laravel/framework.json",
        )
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    def test_package_info_missing_description_fallback(self):
        payload = {
            "packages": {
                "acme/tool": [
                    {"name": "acme/tool", "version": "1.2.3", "time": "2024-01-01T00:00:00+00:00"},
                    {"name": "acme/tool", "description": "An acme tool.", "version": "1.2.2"},
                ]
            }
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.package_info("acme", "tool")
        self.assertIn("Description: An acme tool.", result)
        self.assertIn("Latest version: 1.2.3", result)

    def test_package_info_no_released_line_when_time_missing(self):
        payload = {
            "packages": {
                "acme/tool": [
                    {"name": "acme/tool", "description": "An acme tool.", "version": "1.0.0"},
                ]
            }
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.package_info("acme", "tool")
        self.assertNotIn("Released:", result)
        self.assertIn("Latest version: 1.0.0", result)

    def test_package_info_not_found_404(self):
        with mock.patch("requests.get", return_value=FakeResponse(None, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("acme", "nope")
        self.assertEqual(str(ctx.exception), "ERROR: package 'acme/nope' not found.")

    def test_package_info_empty_versions(self):
        payload = {"packages": {"acme/tool": []}}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("acme", "tool")
        self.assertEqual(str(ctx.exception), "ERROR: package 'acme/tool' not found.")

    def test_package_info_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("acme", "tool")
        self.assertIn("ERROR: Packagist API 500.", str(ctx.exception))

    def test_package_info_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.package_info("acme", "tool")
        self.assertIn("ERROR: Packagist API request failed", str(ctx.exception))

    def test_search_packages(self):
        payload = {
            "results": [
                {"name": "monolog/monolog", "description": "Sends your logs to places."},
                {"name": "phpunit/phpunit", "description": "The PHP Unit Testing framework."},
            ],
            "total": 2,
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.search_packages("log", limit=2)
        self.assertEqual(
            result,
            "monolog/monolog — Sends your logs to places.\n"
            "phpunit/phpunit — The PHP Unit Testing framework.",
        )
        self.assertEqual(get.call_args.args[0], f"{SEARCH_BASE}/search.json?q=log&per_page=2")
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    def test_search_packages_limit_clamped(self):
        results = [{"name": f"acme/pkg-{i}", "description": f"Package {i}."} for i in range(150)]
        payload = {"results": results, "total": 150}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.search_packages("acme", limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertIn("per_page=100", get.call_args.args[0])

    def test_search_packages_limit_min_clamped(self):
        payload = {"results": [{"name": "acme/one", "description": "One."}], "total": 1}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            self.conn.search_packages("acme", limit=0)
        self.assertIn("per_page=1", get.call_args.args[0])

    def test_search_packages_empty(self):
        payload = {"results": [], "total": 0}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            self.assertEqual(
                self.conn.search_packages("xyzzy-no-match"),
                "No packages found for 'xyzzy-no-match'.",
            )

    def test_search_packages_missing_description(self):
        payload = {"results": [{"name": "acme/nodesc"}], "total": 1}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.search_packages("acme"), "acme/nodesc — (no description)")

    def test_search_packages_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=503)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_packages("log")
        self.assertIn("ERROR: Packagist API 503.", str(ctx.exception))

    def test_search_packages_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_packages("log")
        self.assertIn("ERROR: Packagist API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("packagist")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.package_info("acme", "tool")
        self.assertIn("zeline connect packagist", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search_packages("log")


class PackagistRegistryTests(unittest.TestCase):
    def test_packagist_registered(self):
        from zeline.connectors import get

        conn = get("packagist")
        self.assertIsInstance(conn, PackagistConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(packagist_mod.PackagistConnector.id, "packagist")


if __name__ == "__main__":
    unittest.main()
