"""Tests for the BunnyCDN connector (API key via AccessKey header). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import bunnycdn as bunnycdn_mod
from zeline.connectors.bunnycdn import BunnyCDNConnector

TOKEN = "bunny_fake_api_key_123"
PULL_ZONES = [
    {"Id": 1, "Name": "my-cdn", "Hostnames": ["cdn.example.com", "cdn2.example.com"]},
    {"Id": 2, "Name": "assets", "Hostnames": [{"Value": "assets.example.com"}]},
]
STORAGE_ZONES = [
    {"Id": 10, "Name": "videos", "Region": "DE"},
    {"Id": 11, "Name": "backups", "Region": "SG"},
]


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

    store.save("bunnycdn", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class BunnyCDNConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bunnycdn-test-"))
        _patch_store(self, self.tmp)
        self.conn = BunnyCDNConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse(list(PULL_ZONES))) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to BunnyCDN (2 pull zones).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.bunny.net/pullzone")
        self.assertEqual(kwargs["headers"]["AccessKey"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("bunnycdn"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse([])):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to BunnyCDN (0 pull zones).")

    def test_connect_token_kwarg_whitespace_stripped(self):
        with mock.patch("requests.get", return_value=FakeResponse([])) as get:
            result = self.conn.connect(token=f"  {TOKEN}  ")
        self.assertEqual(result, "Connected to BunnyCDN (0 pull zones).")
        self.assertEqual(get.call_args.kwargs["headers"]["AccessKey"], TOKEN)

    def test_connect_missing_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR: no API key"))
        self.assertIsNone(store.load("bunnycdn"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: BunnyCDN rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("bunnycdn"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach https://api.bunny.net"))
        self.assertIsNone(store.load("bunnycdn"))

    def test_connect_non_json_200_still_connects(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to BunnyCDN (0 pull zones).")
        self.assertEqual(store.load("bunnycdn"), {"api_key": TOKEN})

    def test_status_connected_hides_secret(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(),
            {"connected": True, "detail": "API key linked"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "BunnyCDN disconnected.")
        self.assertEqual(self.conn.disconnect(), "BunnyCDN was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "bunnycdn")
        self.assertEqual(self.conn.name, "BunnyCDN")
        self.assertEqual(self.conn.description, "List BunnyCDN pull zones and storage zones.")
        self.assertEqual(self.conn.auth_kind, "pat")


class BunnyCDNOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bunnycdn-test-"))
        _patch_store(self, self.tmp)
        self.conn = BunnyCDNConnector()
        _seed_connected()

    def test_list_pull_zones(self):
        mapping = {("GET", "https://api.bunny.net/pullzone"): (list(PULL_ZONES), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_pull_zones(limit=2)
        self.assertEqual(result, "1: my-cdn (cdn.example.com)\n2: assets (assets.example.com)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.bunny.net/pullzone")
        self.assertEqual(kwargs["headers"]["AccessKey"], TOKEN)
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_pull_zones_no_hostnames(self):
        mapping = {
            ("GET", "https://api.bunny.net/pullzone"): (
                [{"Id": 3, "Name": "bare"}],
                200,
            )
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_pull_zones(), "3: bare (?)")

    def test_list_pull_zones_limit_clamped(self):
        mapping = {
            ("GET", "https://api.bunny.net/pullzone"): (
                [
                    {"Id": i, "Name": f"zone-{i}", "Hostnames": [f"z{i}.example.com"]}
                    for i in range(100)
                ],
                200,
            )
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_pull_zones(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_pull_zones_min_limit(self):
        mapping = {("GET", "https://api.bunny.net/pullzone"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_pull_zones(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 1})

    def test_list_pull_zones_empty(self):
        mapping = {("GET", "https://api.bunny.net/pullzone"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_pull_zones(), "No pull zones found.")

    def test_list_pull_zones_500_raises(self):
        mapping = {("GET", "https://api.bunny.net/pullzone"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_pull_zones()
        self.assertEqual(str(ctx.exception), "ERROR: BunnyCDN API 500 on /pullzone.")

    def test_list_pull_zones_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_pull_zones()
        self.assertIn("ERROR: BunnyCDN API request failed", str(ctx.exception))

    def test_list_pull_zones_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_pull_zones()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_list_storage_zones(self):
        mapping = {("GET", "https://api.bunny.net/storagezone"): (list(STORAGE_ZONES), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_storage_zones(limit=2)
        self.assertEqual(result, "10: videos (region: DE)\n11: backups (region: SG)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.bunny.net/storagezone")
        self.assertEqual(kwargs["headers"]["AccessKey"], TOKEN)
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_storage_zones_limit_clamped(self):
        mapping = {
            ("GET", "https://api.bunny.net/storagezone"): (
                [{"Id": i, "Name": f"sz-{i}", "Region": "DE"} for i in range(100)],
                200,
            )
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_storage_zones(limit=200)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_storage_zones_empty(self):
        mapping = {("GET", "https://api.bunny.net/storagezone"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_storage_zones(), "No storage zones found.")

    def test_list_storage_zones_500_raises(self):
        mapping = {("GET", "https://api.bunny.net/storagezone"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_storage_zones()
        self.assertEqual(str(ctx.exception), "ERROR: BunnyCDN API 500 on /storagezone.")

    def test_list_storage_zones_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_storage_zones()
        self.assertIn("ERROR: BunnyCDN API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("bunnycdn")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_pull_zones()
        self.assertIn("zeline connect bunnycdn", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_storage_zones()

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_pull_zones()
        self.assertNotIn(TOKEN, str(ctx.exception))


class BunnyCDNRegistryTests(unittest.TestCase):
    def test_bunnycdn_registered(self):
        from zeline.connectors import get

        conn = get("bunnycdn")
        self.assertIsInstance(conn, BunnyCDNConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(bunnycdn_mod.BunnyCDNConnector.id, "bunnycdn")


if __name__ == "__main__":
    unittest.main()
