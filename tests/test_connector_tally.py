"""Tests for the Tally connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import tally as tally_mod
from zeline.connectors.tally import TallyConnector

API_BASE = "https://api.tally.so"
TOKEN = "tally_fake_token_123"


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

    store.save("tally", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class TallyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tally-test-"))
        _patch_store(self, self.tmp)
        self.conn = TallyConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"forms": [], "count": 0})
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Tally.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/forms")
        self.assertEqual(kwargs["params"], {"perPage": 1})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("tally"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse({"forms": []})):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Tally.")

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("tally"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.tally.so"))
        self.assertIsNone(store.load("tally"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Tally rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("tally"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Tally disconnected.")
        self.assertEqual(self.conn.disconnect(), "Tally was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "tally")
        self.assertEqual(self.conn.name, "Tally")
        self.assertEqual(self.conn.auth_kind, "pat")


class TallyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tally-test-"))
        _patch_store(self, self.tmp)
        self.conn = TallyConnector()
        _seed_connected()

    def test_list_forms(self):
        mapping = {
            ("GET", f"{API_BASE}/forms"): (
                {"forms": [
                    {"id": "t1", "title": "Form A"},
                    {"id": "t2", "title": "Form B"},
                ], "count": 2},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_forms(limit=2)
        self.assertEqual(result, "t1: Form A\nt2: Form B")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/forms")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"perPage": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_forms_items_fallback(self):
        mapping = {
            ("GET", f"{API_BASE}/forms"): (
                {"items": [{"id": "t9", "title": "Legacy"}]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_forms()
        self.assertEqual(result, "t9: Legacy")

    def test_list_forms_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/forms"): (
                {"forms": [{"id": f"t{i}", "title": f"F{i}"} for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_forms(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"perPage": 100})

    def test_list_forms_min_limit(self):
        mapping = {("GET", f"{API_BASE}/forms"): ({"forms": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_forms(limit=-3)
        self.assertEqual(req.call_args.kwargs["params"], {"perPage": 1})

    def test_list_forms_empty(self):
        mapping = {("GET", f"{API_BASE}/forms"): ({"forms": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_forms(), "No forms found.")

    def test_list_forms_http_error(self):
        mapping = {("GET", f"{API_BASE}/forms"): ({}, 401)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertEqual(str(ctx.exception), "ERROR: Tally API 401 on /forms.")

    def test_list_forms_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertIn("ERROR: Tally API request failed", str(ctx.exception))

    def test_list_forms_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("tally")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_forms()
        self.assertIn("zeline connect tally", str(ctx.exception))

    def test_secret_never_echoed(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertNotIn(TOKEN, str(ctx.exception))


class TallyRegistryTests(unittest.TestCase):
    def test_tally_registered(self):
        from zeline.connectors import get

        conn = get("tally")
        self.assertIsInstance(conn, TallyConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(tally_mod.TallyConnector.id, "tally")


if __name__ == "__main__":
    unittest.main()
