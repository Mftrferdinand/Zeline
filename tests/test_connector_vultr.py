"""Tests for the Vultr connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import vultr as vultr_mod
from zeline.connectors.vultr import VultrConnector

API_BASE = "https://api.vultr.com/v2"
TOKEN = "vultr_fake_token_123"


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

    store.save("vultr", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class VultrConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-vultr-test-"))
        _patch_store(self, self.tmp)
        self.conn = VultrConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"account": {"name": "acme", "email": "a@b.c"}}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Vultr (account acme).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/account")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("vultr"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"account": {"name": "acme"}})
        ):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Vultr (account acme).")

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("vultr"))
        get.assert_not_called()

    def test_connect_non_json_body_does_not_crash(self):
        resp = FakeResponse("<html>ok</html>", status=200)
        resp.json = mock.Mock(side_effect=ValueError("no json"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Vultr (account ?).")

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.vultr.com"))
        self.assertIsNone(store.load("vultr"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Vultr rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("vultr"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_has_no_secret(self):
        _seed_connected()
        self.assertNotIn(TOKEN, str(self.conn.status()))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Vultr disconnected.")
        self.assertEqual(self.conn.disconnect(), "Vultr was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "vultr")
        self.assertEqual(self.conn.name, "Vultr")
        self.assertEqual(self.conn.auth_kind, "pat")


class VultrOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-vultr-test-"))
        _patch_store(self, self.tmp)
        self.conn = VultrConnector()
        _seed_connected()

    def test_list_instances(self):
        mapping = {
            ("GET", f"{API_BASE}/instances"): (
                {"instances": [
                    {
                        "id": "inst-1",
                        "label": "web-1",
                        "status": "active",
                        "region": "ewr",
                        "main_ip": "1.2.3.4",
                    },
                    {
                        "id": "inst-2",
                        "label": "db-1",
                        "status": "stopped",
                        "region": "ams",
                        "main_ip": "5.6.7.8",
                    },
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_instances(limit=2)
        self.assertEqual(
            result,
            "inst-1: web-1 [active] region=ewr ip=1.2.3.4\n"
            "inst-2: db-1 [stopped] region=ams ip=5.6.7.8",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/instances")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_instances_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/instances"): (
                {"instances": [
                    {
                        "id": f"i{i}",
                        "label": f"l{i}",
                        "status": "active",
                        "region": "ewr",
                        "main_ip": f"1.2.3.{i % 255}",
                    }
                    for i in range(100)
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_instances(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 100})

    def test_list_instances_min_limit(self):
        mapping = {("GET", f"{API_BASE}/instances"): ({"instances": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_instances(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 1})

    def test_list_instances_empty(self):
        mapping = {("GET", f"{API_BASE}/instances"): ({"instances": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_instances(), "No instances found.")

    def test_list_instances_missing_fields(self):
        mapping = {
            ("GET", f"{API_BASE}/instances"): ({"instances": [{"id": "inst-9"}]}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_instances()
        self.assertEqual(result, "inst-9: (no label) [?] region=? ip=?")

    def test_list_instances_http_error(self):
        mapping = {("GET", f"{API_BASE}/instances"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_instances()
        self.assertEqual(str(ctx.exception), "ERROR: Vultr API 500 on /instances.")

    def test_list_instances_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_instances()
        self.assertIn("ERROR: Vultr API request failed", str(ctx.exception))

    def test_list_instances_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_instances()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("vultr")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_instances()
        self.assertIn("zeline connect vultr", str(ctx.exception))

    def test_secret_not_in_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_instances()
        self.assertNotIn(TOKEN, str(ctx.exception))


class VultrRegistryTests(unittest.TestCase):
    def test_vultr_registered(self):
        from zeline.connectors import get

        conn = get("vultr")
        self.assertIsInstance(conn, VultrConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(vultr_mod.VultrConnector.id, "vultr")


if __name__ == "__main__":
    unittest.main()
