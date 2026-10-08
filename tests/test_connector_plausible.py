"""Tests for the Plausible connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import plausible as plausible_mod
from zeline.connectors.plausible import PlausibleConnector

API_BASE = "https://plausible.io/api/v1"
TOKEN = "pl_fake_token_123"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("plausible", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class PlausibleConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pl-test-"))
        _patch_store(self, self.tmp)
        self.conn = PlausibleConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"sites": [{"domain": "example.com"}]}),
        ) as fake_get:
            result = self.conn.connect(api_key=TOKEN)
        self.assertNotIn("ERROR", result)
        self.assertIn("1 site", result)
        self.assertEqual(store.load("plausible"), {"api_key": TOKEN})
        fake_get.assert_called_once()
        _, kwargs = fake_get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_connect_accepts_token_alias(self):
        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"sites": []}),
        ):
            result = self.conn.connect(token=TOKEN)
        self.assertNotIn("ERROR", result)
        from zeline.connectors import store

        self.assertEqual(store.load("plausible"), {"api_key": TOKEN})

    def test_connect_rejects_empty_key(self):
        with mock.patch("requests.get") as fake_get:
            result = self.conn.connect(api_key="")
        self.assertTrue(result.startswith("ERROR:"))
        fake_get.assert_not_called()

    def test_connect_401_does_not_save(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"error": "unauthorized"}, status=401),
        ):
            result = self.conn.connect(api_key=TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("plausible"))

    def test_connect_network_error_returns_error(self):
        import requests as requests_mod

        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            side_effect=requests_mod.RequestException("boom"),
        ):
            result = self.conn.connect(api_key=TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("plausible"))

    def test_connect_non_json_200_does_not_crash(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse(ValueError("no json"), status=200),
        ):
            result = self.conn.connect(api_key=TOKEN)
        self.assertNotIn("Traceback", result)
        self.assertFalse(result.startswith("ERROR:"))
        self.assertEqual(store.load("plausible"), {"api_key": TOKEN})

    def test_disconnect_when_connected(self):
        _seed_connected()
        result = self.conn.disconnect()
        self.assertIn("disconnected", result.lower())
        self.assertFalse(self.conn.is_connected())

    def test_disconnect_when_not_connected(self):
        result = self.conn.disconnect()
        self.assertNotIn("ERROR", result)
        self.assertIn("not connected", result.lower())

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        status = self.conn.status()
        self.assertFalse(status["connected"])
        self.assertEqual(status["detail"], "not linked")


class PlausibleOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-pl-test-"))
        _patch_store(self, self.tmp)
        self.conn = PlausibleConnector()
        _seed_connected()

    def test_list_sites_success(self):
        payload = {"sites": [{"domain": "example.com"}, {"domain": "blog.example.com"}]}
        with mock.patch(
            "requests.request",
            return_value=FakeResponse(payload),
        ):
            out = self.conn.list_sites()
        self.assertIn("example.com", out)
        self.assertIn("blog.example.com", out)

    def test_list_sites_empty(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"sites": []}),
        ):
            out = self.conn.list_sites()
        self.assertEqual(out, "No sites found.")

    def test_list_sites_uses_bearer_header(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"sites": []}),
        ) as fake_req:
            self.conn.list_sites()
        _, kwargs = fake_req.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_site_stats_success(self):
        payload = {
            "results": {
                "visitors": {"value": 1234},
                "pageviews": {"value": 5678},
            }
        }
        with mock.patch(
            "requests.request",
            return_value=FakeResponse(payload),
        ):
            out = self.conn.site_stats("example.com", period="30d")
        self.assertIn("example.com", out)
        self.assertIn("1234", out)
        self.assertIn("5678", out)

    def test_site_stats_invalid_period_defaults(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"results": {"visitors": {"value": 9}}}),
        ) as fake_req:
            out = self.conn.site_stats("example.com", period="bogus")
        _, kwargs = fake_req.call_args
        params = kwargs["params"]
        self.assertEqual(params["period"], "7d")
        self.assertIn("7d", out)

    def test_site_stats_sends_expected_params(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"results": {"visitors": {"value": 1}}}),
        ) as fake_req:
            self.conn.site_stats("example.com")
        method, url = fake_req.call_args[0][0], fake_req.call_args[0][1]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/stats/aggregate"))
        params = fake_req.call_args[1]["params"]
        self.assertEqual(params["site_id"], "example.com")
        self.assertIn("visitors", params["metrics"])

    def test_operations_require_connection(self):
        self.conn.disconnect()
        with self.assertRaisesRegex(RuntimeError, "ERROR:"):
            self.conn.list_sites()

    def test_operation_error_500_raises(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"error": "boom"}, status=500),
        ):
            with self.assertRaisesRegex(RuntimeError, "ERROR:"):
                self.conn.list_sites()

    def test_operation_network_error_raises(self):
        import requests as requests_mod

        with mock.patch(
            "requests.request",
            side_effect=requests_mod.RequestException("down"),
        ):
            with self.assertRaisesRegex(RuntimeError, "ERROR:"):
                self.conn.site_stats("example.com")


if __name__ == "__main__":
    unittest.main()
