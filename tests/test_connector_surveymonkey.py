"""Tests for the SurveyMonkey connector (OAuth access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import surveymonkey as surveymonkey_mod
from zeline.connectors.surveymonkey import SurveyMonkeyConnector

API_BASE = "https://api.surveymonkey.com/v3"
TOKEN = "sm_fake_access_token_123"


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

    store.save("surveymonkey", {"access_token": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class SurveyMonkeyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sm-test-"))
        _patch_store(self, self.tmp)
        self.conn = SurveyMonkeyConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"id": "123", "name": "Bob"})
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to SurveyMonkey as Bob.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/users/me")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("surveymonkey"), {"access_token": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse({"id": "9", "name": "Ann"})):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to SurveyMonkey as Ann.")

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("surveymonkey"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.surveymonkey.com"))
        self.assertIsNone(store.load("surveymonkey"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: SurveyMonkey rejected the access token"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("surveymonkey"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(), {"connected": True, "detail": "access token stored"}
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "SurveyMonkey disconnected.")
        self.assertEqual(self.conn.disconnect(), "SurveyMonkey was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "surveymonkey")
        self.assertEqual(self.conn.name, "SurveyMonkey")
        self.assertEqual(self.conn.auth_kind, "pat")


class SurveyMonkeyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-sm-test-"))
        _patch_store(self, self.tmp)
        self.conn = SurveyMonkeyConnector()
        _seed_connected()

    def test_list_surveys(self):
        mapping = {
            ("GET", f"{API_BASE}/surveys"): (
                {"data": [
                    {"id": "sv1", "title": "Survey A"},
                    {"id": "sv2", "title": "Survey B"},
                ], "per_page": 2, "total": 2},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_surveys(limit=2)
        self.assertEqual(result, "sv1: Survey A\nsv2: Survey B")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/surveys")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_surveys_items_fallback(self):
        mapping = {
            ("GET", f"{API_BASE}/surveys"): (
                {"items": [{"id": "sv9", "title": "Legacy"}]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_surveys()
        self.assertEqual(result, "sv9: Legacy")

    def test_list_surveys_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/surveys"): (
                {"data": [{"id": f"sv{i}", "title": f"S{i}"} for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_surveys(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 100})

    def test_list_surveys_min_limit(self):
        mapping = {("GET", f"{API_BASE}/surveys"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_surveys(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 1})

    def test_list_surveys_empty(self):
        mapping = {("GET", f"{API_BASE}/surveys"): ({"data": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_surveys(), "No surveys found.")

    def test_list_surveys_http_error(self):
        mapping = {("GET", f"{API_BASE}/surveys"): ({}, 403)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_surveys()
        self.assertEqual(str(ctx.exception), "ERROR: SurveyMonkey API 403 on /surveys.")

    def test_list_surveys_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_surveys()
        self.assertIn("ERROR: SurveyMonkey API request failed", str(ctx.exception))

    def test_list_surveys_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_surveys()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("surveymonkey")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_surveys()
        self.assertIn("zeline connect surveymonkey", str(ctx.exception))

    def test_secret_never_echoed(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_surveys()
        self.assertNotIn(TOKEN, str(ctx.exception))


class SurveyMonkeyRegistryTests(unittest.TestCase):
    def test_surveymonkey_registered(self):
        from zeline.connectors import get

        conn = get("surveymonkey")
        self.assertIsInstance(conn, SurveyMonkeyConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(surveymonkey_mod.SurveyMonkeyConnector.id, "surveymonkey")


if __name__ == "__main__":
    unittest.main()
