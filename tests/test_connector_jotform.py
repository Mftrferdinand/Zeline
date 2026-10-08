"""Tests for the JotForm connector (apiKey query param). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import jotform as jotform_mod
from zeline.connectors.jotform import JotFormConnector

API_BASE = "https://api.jotform.com"
TOKEN = "jotform_fake_key_123"


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

    store.save("jotform", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class JotFormConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jf-test-"))
        _patch_store(self, self.tmp)
        self.conn = JotFormConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        payload = {"responseCode": 200, "message": "success",
                   "content": {"username": "bob"}}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to JotForm as bob.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/user")
        self.assertEqual(kwargs["params"], {"apiKey": TOKEN})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("jotform"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        payload = {"responseCode": 200, "message": "success",
                   "content": {"username": "carol"}}
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to JotForm as carol.")

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("jotform"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.jotform.com"))
        self.assertIsNone(store.load("jotform"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-key")
        self.assertTrue(result.startswith("ERROR: JotForm rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("jotform"))

    def test_connect_bad_key_envelope_stores_nothing(self):
        from zeline.connectors import store

        payload = {"responseCode": 401, "message": "Invalid API key", "content": {}}
        with mock.patch("requests.get", return_value=FakeResponse(payload, status=200)):
            result = self.conn.connect("bogus-key")
        self.assertTrue(result.startswith("ERROR: JotForm rejected the API key"))
        self.assertIn("Invalid API key", result)
        self.assertNotIn("bogus-key", result)
        self.assertIsNone(store.load("jotform"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "JotForm disconnected.")
        self.assertEqual(self.conn.disconnect(), "JotForm was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "jotform")
        self.assertEqual(self.conn.name, "JotForm")
        self.assertEqual(self.conn.auth_kind, "pat")


class JotFormOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jf-test-"))
        _patch_store(self, self.tmp)
        self.conn = JotFormConnector()
        _seed_connected()

    def test_list_forms(self):
        mapping = {
            ("GET", f"{API_BASE}/user/forms"): (
                {"responseCode": 200, "message": "success", "content": {
                    "111": {"id": "111", "title": "Contact Form"},
                    "222": {"id": "222", "title": "Survey"},
                }},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_forms(limit=2)
        self.assertEqual(result, "111: Contact Form\n222: Survey")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/user/forms")
        self.assertEqual(kwargs["params"], {"limit": 2, "apiKey": TOKEN})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_forms_limit_clamped(self):
        content = {str(i): {"id": str(i), "title": f"F{i}"} for i in range(100)}
        mapping = {
            ("GET", f"{API_BASE}/user/forms"): (
                {"responseCode": 200, "message": "success", "content": content},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_forms(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"]["limit"], 100)
        self.assertEqual(req.call_args.kwargs["params"]["apiKey"], TOKEN)

    def test_list_forms_min_limit(self):
        mapping = {
            ("GET", f"{API_BASE}/user/forms"): (
                {"responseCode": 200, "message": "success", "content": {}},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_forms(limit=0)
        self.assertEqual(req.call_args.kwargs["params"]["limit"], 1)

    def test_list_forms_empty(self):
        mapping = {
            ("GET", f"{API_BASE}/user/forms"): (
                {"responseCode": 200, "message": "success", "content": {}},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_forms(), "No forms found.")

    def test_list_forms_http_error(self):
        mapping = {("GET", f"{API_BASE}/user/forms"): ({}, 403)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertEqual(str(ctx.exception), "ERROR: JotForm API 403 on /user/forms.")

    def test_list_forms_envelope_error(self):
        mapping = {
            ("GET", f"{API_BASE}/user/forms"): (
                {"responseCode": 401, "message": "Unauthorized", "content": {}},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertIn("ERROR: JotForm API 401", str(ctx.exception))

    def test_list_forms_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertIn("ERROR: JotForm API request failed", str(ctx.exception))

    def test_get_submissions(self):
        mapping = {
            ("GET", f"{API_BASE}/form/111/submissions"): (
                {"responseCode": 200, "message": "success", "content": [
                    {"id": "s1", "created_at": "2026-10-01 10:00:00"},
                    {"id": "s2", "created_at": "2026-10-02 11:30:00"},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.get_submissions("111", limit=2)
        self.assertEqual(
            result,
            "s1 (created 2026-10-01 10:00:00)\ns2 (created 2026-10-02 11:30:00)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/form/111/submissions")
        self.assertEqual(kwargs["params"], {"limit": 2, "apiKey": TOKEN})

    def test_get_submissions_empty(self):
        mapping = {
            ("GET", f"{API_BASE}/form/111/submissions"): (
                {"responseCode": 200, "message": "success", "content": []},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(
                self.conn.get_submissions("111"), "No submissions found for form 111."
            )

    def test_get_submissions_http_error(self):
        mapping = {("GET", f"{API_BASE}/form/nope/submissions"): ({}, 404)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_submissions("nope")
        self.assertIn("ERROR: JotForm API 404", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("jotform")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_forms()
        self.assertIn("zeline connect jotform", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_submissions("111")

    def test_secret_never_echoed(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertNotIn(TOKEN, str(ctx.exception))


class JotFormRegistryTests(unittest.TestCase):
    def test_jotform_registered(self):
        from zeline.connectors import get

        conn = get("jotform")
        self.assertIsInstance(conn, JotFormConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(jotform_mod.JotFormConnector.id, "jotform")


if __name__ == "__main__":
    unittest.main()
