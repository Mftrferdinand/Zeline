"""Tests for the Typeform connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import typeform as typeform_mod
from zeline.connectors.typeform import TypeformConnector

API_BASE = "https://api.typeform.com"
TOKEN = "tf_fake_token_123"


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

    store.save("typeform", {"api_key": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class TypeformConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tf-test-"))
        _patch_store(self, self.tmp)
        self.conn = TypeformConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"user_id": "u42", "email": "a@b.c"}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Typeform (user u42).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/me")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("typeform"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"user_id": "u9"})
        ):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Typeform (user u9).")

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("typeform"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.typeform.com"))
        self.assertIsNone(store.load("typeform"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Typeform rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("typeform"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Typeform disconnected.")
        self.assertEqual(self.conn.disconnect(), "Typeform was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "typeform")
        self.assertEqual(self.conn.name, "Typeform")
        self.assertEqual(self.conn.auth_kind, "pat")


class TypeformOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-tf-test-"))
        _patch_store(self, self.tmp)
        self.conn = TypeformConnector()
        _seed_connected()

    def test_list_forms(self):
        mapping = {
            ("GET", f"{API_BASE}/forms"): (
                {"items": [
                    {"id": "f1", "title": "Form One"},
                    {"id": "f2", "title": "Form Two"},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_forms(limit=2)
        self.assertEqual(result, "f1: Form One\nf2: Form Two")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/forms")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["params"], {"page_size": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_forms_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/forms"): (
                {"items": [{"id": f"f{i}", "title": f"F{i}"} for _ in [0] for i in range(100)]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_forms(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"page_size": 100})

    def test_list_forms_min_limit(self):
        mapping = {("GET", f"{API_BASE}/forms"): ({"items": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_forms(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"page_size": 1})

    def test_list_forms_empty(self):
        mapping = {("GET", f"{API_BASE}/forms"): ({"items": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_forms(), "No forms found.")

    def test_list_forms_http_error(self):
        mapping = {("GET", f"{API_BASE}/forms"): ({}, 403)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertEqual(str(ctx.exception), "ERROR: Typeform API 403 on /forms.")

    def test_list_forms_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertIn("ERROR: Typeform API request failed", str(ctx.exception))

    def test_list_forms_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_get_responses(self):
        mapping = {
            ("GET", f"{API_BASE}/forms/f1/responses"): (
                {"items": [
                    {"response_id": "r1", "answers": [{}, {}, {}]},
                    {"response_id": "r2", "answers": []},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.get_responses("f1", limit=2)
        self.assertEqual(result, "r1 (3 answers)\nr2 (0 answers)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/forms/f1/responses")
        self.assertEqual(kwargs["params"], {"page_size": 2})

    def test_get_responses_missing_answers_field(self):
        mapping = {
            ("GET", f"{API_BASE}/forms/f9/responses"): ({"items": [{"response_id": "r7"}]}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.get_responses("f9")
        self.assertEqual(result, "r7 (0 answers)")

    def test_get_responses_empty(self):
        mapping = {
            ("GET", f"{API_BASE}/forms/f1/responses"): ({"items": []}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.get_responses("f1"), "No responses found for form f1.")

    def test_get_responses_http_error(self):
        mapping = {
            ("GET", f"{API_BASE}/forms/nope/responses"): ({}, 404),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_responses("nope")
        self.assertIn("ERROR: Typeform API 404", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("typeform")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_forms()
        self.assertIn("zeline connect typeform", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_responses("f1")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_forms()
        self.assertNotIn(TOKEN, str(ctx.exception))


class TypeformRegistryTests(unittest.TestCase):
    def test_typeform_registered(self):
        from zeline.connectors import get

        conn = get("typeform")
        self.assertIsInstance(conn, TypeformConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(typeform_mod.TypeformConnector.id, "typeform")


if __name__ == "__main__":
    unittest.main()
