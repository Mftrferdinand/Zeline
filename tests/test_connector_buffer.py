"""Tests for the Buffer connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import buffer as buffer_mod
from zeline.connectors.buffer import BufferConnector

API_BASE = "https://api.bufferapp.com/1"
TOKEN = "buf_fake_token_123"


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

    store.save("buffer", {"access_token": TOKEN})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class BufferConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-buf-test-"))
        _patch_store(self, self.tmp)
        self.conn = BufferConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"id": "u42", "email": "a@b.c"}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Buffer (user u42).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/user.json")
        self.assertEqual(kwargs["params"], {"access_token": TOKEN})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("buffer"), {"access_token": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse({"id": "u9"})):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Buffer (user u9).")

    def test_connect_access_token_kwarg(self):
        with mock.patch("requests.get", return_value=FakeResponse({"id": "u10"})):
            result = self.conn.connect(access_token=TOKEN)
        self.assertEqual(result, "Connected to Buffer (user u10).")

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("buffer"))
        get.assert_not_called()

    def test_connect_non_json_200_does_not_crash(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Buffer (user ?).")

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach api.bufferapp.com"))
        self.assertIsNone(store.load("buffer"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Buffer rejected the access token"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("buffer"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "access token stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Buffer disconnected.")
        self.assertEqual(self.conn.disconnect(), "Buffer was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "buffer")
        self.assertEqual(self.conn.name, "Buffer")
        self.assertEqual(self.conn.auth_kind, "pat")


class BufferOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-buf-test-"))
        _patch_store(self, self.tmp)
        self.conn = BufferConnector()
        _seed_connected()

    def test_list_profiles(self):
        mapping = {
            ("GET", f"{API_BASE}/profiles.json"): (
                [
                    {"id": "p1", "service": "twitter", "service_username": "@one"},
                    {"id": "p2", "service": "linkedin", "username": "User Two"},
                ],
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_profiles()
        self.assertEqual(result, "p1: twitter (@one)\np2: linkedin (User Two)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/profiles.json")
        self.assertEqual(kwargs["params"], {"access_token": TOKEN})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_profiles_limit_clamped(self):
        profiles = [{"id": f"p{i}", "service": "twitter", "service_username": f"@u{i}"} for i in range(100)]
        mapping = {("GET", f"{API_BASE}/profiles.json"): (profiles, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_profiles(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["timeout"], 30)

    def test_list_profiles_empty(self):
        mapping = {("GET", f"{API_BASE}/profiles.json"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_profiles(), "No profiles found.")

    def test_list_profiles_http_error(self):
        mapping = {("GET", f"{API_BASE}/profiles.json"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_profiles()
        self.assertEqual(str(ctx.exception), "ERROR: Buffer API 500 on /profiles.json.")

    def test_create_post_success(self):
        mapping = {
            ("POST", f"{API_BASE}/updates/create.json"): ({"success": True, "updates": [{"id": "x1"}]}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.create_post("hello world", ["p1", "p2"])
        self.assertIn("Post queued", result)
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/updates/create.json")
        self.assertEqual(kwargs["json"], {"text": "hello world", "profile_ids": ["p1", "p2"]})
        self.assertEqual(kwargs["params"], {"access_token": TOKEN})

    def test_create_post_profile_ids_coerced_to_str(self):
        mapping = {
            ("POST", f"{API_BASE}/updates/create.json"): ({"success": True}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.create_post("hi", [123])
        self.assertEqual(req.call_args.kwargs["json"]["profile_ids"], ["123"])

    def test_create_post_empty_profile_ids_no_api_call(self):
        with mock.patch("requests.request") as req:
            result = self.conn.create_post("hello world", [])
        self.assertEqual(result, "ERROR: profile_ids required.")
        req.assert_not_called()

    def test_create_post_none_profile_ids_no_api_call(self):
        with mock.patch("requests.request") as req:
            result = self.conn.create_post("hello world", None)
        self.assertEqual(result, "ERROR: profile_ids required.")
        req.assert_not_called()

    def test_create_post_http_error(self):
        mapping = {("POST", f"{API_BASE}/updates/create.json"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_post("hi", ["p1"])
        self.assertIn("ERROR: Buffer API 500", str(ctx.exception))

    def test_create_post_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_post("hi", ["p1"])
        self.assertIn("ERROR: Buffer API request failed", str(ctx.exception))

    def test_create_post_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_post("hi", ["p1"])
        self.assertIn("unreadable response", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("buffer")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_profiles()
        self.assertIn("zeline connect buffer", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_post("hi", ["p1"])

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_profiles()
        self.assertNotIn(TOKEN, str(ctx.exception))


class BufferRegistryTests(unittest.TestCase):
    def test_buffer_registered(self):
        from zeline.connectors import get

        conn = get("buffer")
        self.assertIsInstance(conn, BufferConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(buffer_mod.BufferConnector.id, "buffer")


if __name__ == "__main__":
    unittest.main()
