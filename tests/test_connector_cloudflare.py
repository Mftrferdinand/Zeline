"""Tests for the Cloudflare connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.cloudflare import CloudflareConnector


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

    store.save("cloudflare", {"token": "SECRET-TOKEN"})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cloudflare-test-"))
        _patch_store(self, self.tmp)
        self.conn = CloudflareConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"success": True, "result": {"id": "abc", "status": "active"}})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="SECRET-TOKEN")
        self.assertEqual(result, "Connected to Cloudflare.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.cloudflare.com/client/v4/user/tokens/verify")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        self.assertEqual(store.load("cloudflare")["token"], "SECRET-TOKEN")

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse(
            {
                "success": False,
                "errors": [{"code": 6003, "message": "Invalid token"}],
            },
            status=200,
        )
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("cloudflare"))

    def test_connect_http_rejection_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("cloudflare"))

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
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
        self.assertEqual(self.conn.disconnect(), "Cloudflare disconnected.")
        self.assertEqual(self.conn.disconnect(), "Cloudflare was not connected.")


class ZonesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cloudflare-test-"))
        _patch_store(self, self.tmp)
        self.conn = CloudflareConnector()
        _seed_connected(self.tmp)

    def test_list_zones_formats(self):
        fake = FakeResponse(
            {
                "success": True,
                "result": [
                    {"id": "zone-1", "name": "acme.test", "status": "active"},
                    {"id": "zone-2", "name": "beta.test", "status": "pending"},
                ],
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_zones()
        self.assertEqual(
            result,
            "zone-1: acme.test [active]\nzone-2: beta.test [pending]",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.cloudflare.com/client/v4/zones")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")

    def test_list_zones_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"success": True, "result": []})):
            self.assertEqual(self.conn.list_zones(), "No zones found.")

    def test_list_zones_success_false_raises(self):
        fake = FakeResponse(
            {"success": False, "errors": [{"message": "Request failed"}]},
            status=200,
        )
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_zones()
        self.assertIn("Request failed", str(ctx.exception))

    def test_list_zones_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_zones()
        self.assertIn("ERROR: Cloudflare API 403", str(ctx.exception))

    def test_list_dns_records_formats(self):
        fake = FakeResponse(
            {
                "success": True,
                "result": [
                    {"type": "A", "name": "acme.test", "content": "93.184.216.34"},
                    {"type": "TXT", "name": "acme.test", "content": "v=spf1 -all"},
                ],
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_dns_records("zone-1")
        self.assertEqual(
            result,
            "A acme.test → 93.184.216.34\nTXT acme.test → v=spf1 -all",
        )
        args, kwargs = req.call_args
        self.assertEqual(
            args[1],
            "https://api.cloudflare.com/client/v4/zones/zone-1/dns_records",
        )

    def test_list_dns_records_empty_zone_id(self):
        self.assertTrue(self.conn.list_dns_records("").startswith("ERROR:"))
        self.assertTrue(self.conn.list_dns_records("   ").startswith("ERROR:"))

    def test_list_dns_records_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"success": True, "result": []})):
            result = self.conn.list_dns_records("zone-1")
        self.assertEqual(result, "No DNS records found in zone zone-1.")

    def test_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("cloudflare")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_zones()
        self.assertIn("zeline connect cloudflare", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
