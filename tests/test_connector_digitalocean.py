"""Tests for the DigitalOcean connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import digitalocean as digitalocean_mod
from zeline.connectors.digitalocean import DigitalOceanConnector

API_BASE = "https://api.digitalocean.com/v2"
TOKEN = "do_fake_token_123"


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

    store.save("digitalocean", {"token": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


DROPLET = {
    "id": 12345,
    "name": "web-1",
    "status": "active",
    "region": {"slug": "nyc3"},
    "networks": {
        "v4": [
            {"ip_address": "10.0.0.5", "type": "private"},
            {"ip_address": "203.0.113.7", "type": "public"},
        ]
    },
}


class DigitalOceanConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-do-test-"))
        _patch_store(self, self.tmp)
        self.conn = DigitalOceanConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"account": {"email": "a@b.c"}}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to DigitalOcean (account a@b.c).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/account")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("digitalocean"), {"token": TOKEN})

    def test_connect_success_non_json_body_does_not_crash(self):
        from zeline.connectors import store

        resp = FakeResponse("<html>ok</html>", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to DigitalOcean (account ?).")
        self.assertEqual(store.load("digitalocean"), {"token": TOKEN})

    def test_connect_unauthorized_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({}, status=401)
        ) as get:
            with mock.patch(
                "zeline.connectors.store.save", wraps=store.save
            ) as save:
                result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: DigitalOcean rejected the token"))
        self.assertIn("401", result)
        get.assert_called_once()
        save.assert_not_called()
        self.assertIsNone(store.load("digitalocean"))

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("digitalocean"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.digitalocean.com"))
        self.assertIsNone(store.load("digitalocean"))

    def test_status_connected_has_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status, {"connected": True, "detail": "token stored"})
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "DigitalOcean disconnected.")
        self.assertEqual(self.conn.disconnect(), "DigitalOcean was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "digitalocean")
        self.assertEqual(self.conn.name, "DigitalOcean")
        self.assertEqual(self.conn.auth_kind, "pat")
        self.assertEqual(self.conn.description, "Read DigitalOcean droplets.")


class DigitalOceanOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-do-test-"))
        _patch_store(self, self.tmp)
        self.conn = DigitalOceanConnector()
        _seed_connected()

    def test_list_droplets(self):
        mapping = {
            ("GET", f"{API_BASE}/droplets"): (
                {"droplets": [
                    DROPLET,
                    {
                        "id": 67890,
                        "name": "db-1",
                        "status": "off",
                        "region": {"slug": "sgp1"},
                        "networks": {"v4": [{"ip_address": "198.51.100.9", "type": "public"}]},
                    },
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_droplets(limit=2)
        self.assertEqual(
            result,
            "12345: web-1 [active] nyc3 (203.0.113.7)\n67890: db-1 [off] sgp1 (198.51.100.9)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/droplets")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_droplets_missing_fields_defensive(self):
        mapping = {
            ("GET", f"{API_BASE}/droplets"): (
                {"droplets": [{"id": 1}, "not-a-dict"]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_droplets()
        self.assertEqual(result, "1: (no name) [?] ? (?)")

    def test_list_droplets_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/droplets"): (
                {"droplets": [{"id": i, "name": f"d{i}"} for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_droplets(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 100})

    def test_list_droplets_min_limit(self):
        mapping = {("GET", f"{API_BASE}/droplets"): ({"droplets": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_droplets(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 1})

    def test_list_droplets_empty(self):
        mapping = {("GET", f"{API_BASE}/droplets"): ({"droplets": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_droplets(), "No droplets found.")

    def test_list_droplets_http_error(self):
        mapping = {("GET", f"{API_BASE}/droplets"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_droplets()
        self.assertEqual(str(ctx.exception), "ERROR: DigitalOcean API 500 on /droplets.")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_list_droplets_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_droplets()
        self.assertIn("ERROR: DigitalOcean API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("digitalocean")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_droplets()
        self.assertIn("zeline connect digitalocean", str(ctx.exception))


class DigitalOceanRegistryTests(unittest.TestCase):
    def test_digitalocean_registered(self):
        from zeline.connectors import get

        conn = get("digitalocean")
        self.assertIsInstance(conn, DigitalOceanConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(digitalocean_mod.DigitalOceanConnector.id, "digitalocean")


if __name__ == "__main__":
    unittest.main()
