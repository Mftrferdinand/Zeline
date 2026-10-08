"""Tests for the Healthchecks.io connector (API key in X-Api-Key header). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import healthchecks as healthchecks_mod
from zeline.connectors.healthchecks import HealthchecksConnector

API_BASE = "https://healthchecks.io/api/v1"
TOKEN = "hc_fake_token_123"


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

    store.save("healthchecks", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


CHECKS_PAYLOAD = {
    "checks": [
        {
            "name": "Cron job",
            "slug": "cron-job",
            "status": "up",
            "last_ping": "2026-10-08T03:00:00+00:00",
        },
        {
            "name": "Backup",
            "slug": "backup",
            "status": "down",
            "last_ping": None,
        },
    ]
}


class HealthchecksConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-healthchecks-test-"))
        _patch_store(self, self.tmp)
        self.conn = HealthchecksConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"checks": [{"name": "x"}]})
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Healthchecks (1 checks found).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/checks/")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("healthchecks"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse({"checks": []})):
            result = self.conn.connect(token=TOKEN)
        self.assertTrue(result.startswith("Connected to Healthchecks"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("healthchecks"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach healthchecks.io"))
        self.assertIsNone(store.load("healthchecks"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Healthchecks rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("healthchecks"))

    def test_connect_non_json_stores_nothing(self):
        from zeline.connectors import store

        resp = FakeResponse("<html>not json</html>", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("healthchecks"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Healthchecks disconnected.")
        self.assertEqual(self.conn.disconnect(), "Healthchecks was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "healthchecks")
        self.assertEqual(self.conn.name, "Healthchecks")
        self.assertEqual(self.conn.description, "Read Healthchecks.io checks.")
        self.assertEqual(self.conn.auth_kind, "pat")


class HealthchecksOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-healthchecks-test-"))
        _patch_store(self, self.tmp)
        self.conn = HealthchecksConnector()
        _seed_connected()

    def test_list_checks(self):
        mapping = {("GET", f"{API_BASE}/checks/"): (CHECKS_PAYLOAD, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_checks(limit=2)
        self.assertEqual(
            result,
            "Cron job: up (last ping: 2026-10-08T03:00:00+00:00)\n"
            "Backup: down (last ping: never)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/checks/")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_checks_limit_clamped_client_side(self):
        mapping = {
            ("GET", f"{API_BASE}/checks/"): (
                {"checks": [{"name": f"c{i}", "status": "up", "last_ping": None} for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_checks(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertNotIn("limit", req.call_args.kwargs.get("params") or {})

    def test_list_checks_min_limit(self):
        mapping = {("GET", f"{API_BASE}/checks/"): (CHECKS_PAYLOAD, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_checks(limit=-3)
        self.assertEqual(len(result.splitlines()), 1)

    def test_list_checks_empty(self):
        mapping = {("GET", f"{API_BASE}/checks/"): ({"checks": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_checks(), "No checks found.")

    def test_list_checks_defensive_parse(self):
        mapping = {
            ("GET", f"{API_BASE}/checks/"): (
                {
                    "checks": [
                        {"slug": "no-name-check", "status": "paused"},
                        "garbage-entry",
                        {"status": "new"},
                    ]
                },
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_checks()
        self.assertEqual(
            result,
            "no-name-check: paused (last ping: never)\n"
            "(no name): new (last ping: never)",
        )

    def test_list_checks_http_error(self):
        mapping = {("GET", f"{API_BASE}/checks/"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_checks()
        self.assertEqual(str(ctx.exception), "ERROR: Healthchecks API 500 on /checks/.")

    def test_list_checks_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_checks()
        self.assertIn("ERROR: Healthchecks API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("healthchecks")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_checks()
        self.assertIn("zeline connect healthchecks", str(ctx.exception))

    def test_secret_never_echoed(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_checks()
        self.assertNotIn(TOKEN, str(ctx.exception))


class HealthchecksRegistryTests(unittest.TestCase):
    def test_healthchecks_registered(self):
        from zeline.connectors import get

        conn = get("healthchecks")
        self.assertIsInstance(conn, HealthchecksConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(healthchecks_mod.HealthchecksConnector.id, "healthchecks")


if __name__ == "__main__":
    unittest.main()
