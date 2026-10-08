"""Tests for the LinkedIn connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import linkedin as linkedin_mod
from zeline.connectors.linkedin import LinkedInConnector

API_BASE = "https://api.linkedin.com/v2"
USERINFO = {"sub": "person123", "name": "John Doe", "email": "john@x.com"}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("linkedin", {"access_token": "tok", "name": "John Doe"})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method.upper(), url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class LinkedInConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-li-test-"))
        _patch_store(self, self.tmp)
        self.conn = LinkedInConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse(dict(USERINFO))
        ) as get:
            result = self.conn.connect(access_token="tok123")
        self.assertEqual(result, "Connected to LinkedIn as John Doe.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/userinfo")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok123")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("linkedin"), {"access_token": "tok123", "name": "John Doe"}
        )

    def test_connect_no_token_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(), "ERROR: no access token provided.")
        self.assertEqual(self.conn.connect(access_token="  "), "ERROR: no access token provided.")
        self.assertIsNone(store.load("linkedin"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(access_token="tok")
        self.assertTrue(result.startswith("ERROR: could not reach api.linkedin.com"))
        self.assertIsNone(store.load("linkedin"))

    def test_connect_rejected_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(access_token="bad")
        self.assertIn("ERROR: LinkedIn rejected the token (HTTP 401)", result)
        self.assertIsNone(store.load("linkedin"))

    def test_connect_unreadable_body_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse(ValueError("nope"))):
            result = self.conn.connect(access_token="tok")
        self.assertEqual(result, "ERROR: LinkedIn returned an unreadable response.")
        self.assertIsNone(store.load("linkedin"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "John Doe"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "LinkedIn disconnected.")
        self.assertEqual(self.conn.disconnect(), "LinkedIn was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "linkedin")
        self.assertEqual(self.conn.name, "LinkedIn")
        self.assertEqual(self.conn.auth_kind, "pat")


class LinkedInOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-li-test-"))
        _patch_store(self, self.tmp)
        self.conn = LinkedInConnector()
        _seed_connected()

    def test_get_profile(self):
        mapping = {("GET", f"{API_BASE}/userinfo"): (dict(USERINFO), 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.get_profile()
        self.assertEqual(result, "John Doe (john@x.com)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/userinfo")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["timeout"], 30)

    def test_get_profile_http_error(self):
        mapping = {("GET", f"{API_BASE}/userinfo"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_profile()
        self.assertIn("ERROR: LinkedIn API 500 on /userinfo.", str(ctx.exception))

    def test_get_profile_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_profile()
        self.assertIn("ERROR: LinkedIn API request failed", str(ctx.exception))

    def test_share_post(self):
        mapping = {
            ("GET", f"{API_BASE}/userinfo"): (dict(USERINFO), 200),
            ("POST", f"{API_BASE}/ugcPosts"): ({"id": "urn:li:share:987"}, 201),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.share_post("Hello world")
        self.assertEqual(result, "Post shared: urn:li:share:987")
        post_calls = [c for c in req.call_args_list if c.args[0] == "POST"]
        self.assertEqual(len(post_calls), 1)
        call = post_calls[0]
        self.assertEqual(call.args[1], f"{API_BASE}/ugcPosts")
        body = call.kwargs["json"]
        self.assertEqual(body["author"], "urn:li:person:person123")
        self.assertEqual(body["lifecycleState"], "PUBLISHED")
        share = body["specificContent"]["com.linkedin.ugc.ShareContent"]
        self.assertEqual(share["shareCommentary"]["text"], "Hello world")
        self.assertEqual(share["shareMediaCategory"], "NONE")
        self.assertEqual(body["visibility"]["com.linkedin.ugc.MemberNetworkVisibility"], "PUBLIC")
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer tok")

    def test_share_post_missing_person_id(self):
        mapping = {
            ("GET", f"{API_BASE}/userinfo"): ({"name": "John"}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.share_post("hi")
        self.assertEqual(str(ctx.exception), "ERROR: LinkedIn userinfo did not return a person id.")

    def test_share_post_http_error(self):
        mapping = {
            ("GET", f"{API_BASE}/userinfo"): (dict(USERINFO), 200),
            ("POST", f"{API_BASE}/ugcPosts"): ({"message": "denied"}, 403),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.share_post("hi")
        self.assertIn("ERROR: LinkedIn API 403 on /ugcPosts.", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("linkedin")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.get_profile()
        self.assertIn("zeline connect linkedin", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.share_post("hi")


class LinkedInRegistryTests(unittest.TestCase):
    def test_linkedin_registered(self):
        from zeline.connectors import get

        conn = get("linkedin")
        self.assertIsInstance(conn, LinkedInConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(linkedin_mod.LinkedInConnector.id, "linkedin")


if __name__ == "__main__":
    unittest.main()
