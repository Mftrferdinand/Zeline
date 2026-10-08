"""Tests for the Hunter connector (API key as query param). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.hunter import HunterConnector

API_BASE = "https://api.hunter.io/v2"
API_KEY = "hunter-test-key"
LOGIN = "owner@example.com"
DOMAIN = "example.com"

ACCOUNT = {"data": {"email": LOGIN, "plan_name": "Free"}}


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

    store.save("hunter", {"api_key": API_KEY, "login": LOGIN})


def _request_side_effect(mapping):
    def _side_effect(url, *args, **kwargs):
        if url in mapping:
            payload, status = mapping[url]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected GET {url}")

    return _side_effect


class HunterConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hunter-test-"))
        _patch_store(self, self.tmp)
        self.conn = HunterConnector()

    def test_connect_success_saves_key_and_login(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse(ACCOUNT)
        ) as get:
            result = self.conn.connect(API_KEY)
        self.assertEqual(result, f"Connected to Hunter as {LOGIN}.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/account")
        self.assertEqual(kwargs["params"], {"api_key": API_KEY})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("hunter"), {"api_key": API_KEY, "login": LOGIN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse(ACCOUNT)):
            result = self.conn.connect(token=f"  {API_KEY}  ")
        self.assertEqual(result, f"Connected to Hunter as {LOGIN}.")

    def test_connect_no_email_still_connects(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"data": {}})
        ):
            result = self.conn.connect(API_KEY)
        self.assertEqual(result, "Connected to Hunter.")
        self.assertEqual(store.load("hunter"), {"api_key": API_KEY, "login": ""})

    def test_connect_missing_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR: no API key"))
        self.assertIsNone(store.load("hunter"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"errors": []}, status=401)
        ) as get:
            result = self.conn.connect("bogus-key")
        self.assertTrue(result.startswith("ERROR: Hunter rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("hunter"))
        get.assert_called_once()

    def test_connect_422_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"errors": []}, status=422)
        ):
            result = self.conn.connect("bad-key")
        self.assertTrue(result.startswith("ERROR: Hunter rejected the API key"))
        self.assertIn("422", result)
        self.assertIsNone(store.load("hunter"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(API_KEY)
        self.assertTrue(result.startswith("ERROR: could not reach Hunter API"))
        self.assertIsNone(store.load("hunter"))

    def test_connect_unreadable_body_still_connects(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(API_KEY)
        self.assertEqual(result, "Connected to Hunter.")
        self.assertEqual(store.load("hunter"), {"api_key": API_KEY, "login": ""})

    def test_status_connected_no_secret(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(),
            {"connected": True, "detail": f"linked as {LOGIN}"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Hunter disconnected.")
        self.assertEqual(self.conn.disconnect(), "Hunter was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "hunter")
        self.assertEqual(self.conn.name, "Hunter")
        self.assertEqual(
            self.conn.description, "Find and verify professional email addresses."
        )
        self.assertEqual(self.conn.auth_kind, "pat")


class HunterOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hunter-test-"))
        _patch_store(self, self.tmp)
        self.conn = HunterConnector()
        _seed_connected()

    def test_domain_search(self):
        payload = {
            "data": {
                "emails": [
                    {"value": "alice@example.com", "type": "personal", "confidence": 92},
                    {"value": "bob@example.com", "type": "generic", "confidence": 81},
                ]
            }
        }
        mapping = {(f"{API_BASE}/domain-search"): (payload, 200)}
        with mock.patch(
            "requests.get", side_effect=_request_side_effect(mapping)
        ) as get:
            result = self.conn.domain_search(DOMAIN, limit=10)
        self.assertEqual(
            result,
            "alice@example.com (type: personal, confidence: 92)\n"
            "bob@example.com (type: generic, confidence: 81)",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/domain-search")
        self.assertEqual(
            kwargs["params"],
            {"domain": DOMAIN, "limit": 10, "api_key": API_KEY},
        )
        self.assertEqual(kwargs["timeout"], 30)

    def test_domain_search_empty(self):
        mapping = {(f"{API_BASE}/domain-search"): ({"data": {"emails": []}}, 200)}
        with mock.patch("requests.get", side_effect=_request_side_effect(mapping)):
            result = self.conn.domain_search(DOMAIN)
        self.assertEqual(result, f"No email addresses found for {DOMAIN}.")

    def test_domain_search_limit_clamped_high(self):
        mapping = {(f"{API_BASE}/domain-search"): ({"data": {"emails": []}}, 200)}
        with mock.patch(
            "requests.get", side_effect=_request_side_effect(mapping)
        ) as get:
            self.conn.domain_search(DOMAIN, limit=500)
        self.assertEqual(get.call_args.kwargs["params"]["limit"], 100)

    def test_domain_search_limit_clamped_low(self):
        mapping = {(f"{API_BASE}/domain-search"): ({"data": {"emails": []}}, 200)}
        with mock.patch(
            "requests.get", side_effect=_request_side_effect(mapping)
        ) as get:
            self.conn.domain_search(DOMAIN, limit=0)
        self.assertEqual(get.call_args.kwargs["params"]["limit"], 1)

    def test_domain_search_400_raises(self):
        mapping = {(f"{API_BASE}/domain-search"): ({"errors": []}, 400)}
        with mock.patch("requests.get", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.domain_search(DOMAIN)
        self.assertEqual(str(ctx.exception), "ERROR: Hunter API 400 on /domain-search.")

    def test_domain_search_network_error_raises(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.domain_search(DOMAIN)
        self.assertTrue(str(ctx.exception).startswith("ERROR: Hunter API request failed"))

    def test_domain_search_not_connected_raises(self):
        self.conn.disconnect()
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.domain_search(DOMAIN)
        self.assertTrue(str(ctx.exception).startswith("ERROR: Hunter is not connected"))

    def test_verify_email(self):
        payload = {"data": {"email": "alice@example.com", "result": "deliverable", "score": 87}}
        mapping = {(f"{API_BASE}/email-verifier"): (payload, 200)}
        with mock.patch(
            "requests.get", side_effect=_request_side_effect(mapping)
        ) as get:
            result = self.conn.verify_email("alice@example.com")
        self.assertEqual(
            result, "alice@example.com — result: deliverable, score: 87"
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/email-verifier")
        self.assertEqual(
            kwargs["params"],
            {"email": "alice@example.com", "api_key": API_KEY},
        )
        self.assertEqual(kwargs["timeout"], 30)

    def test_verify_email_404_raises(self):
        mapping = {(f"{API_BASE}/email-verifier"): ({"errors": []}, 404)}
        with mock.patch("requests.get", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.verify_email("ghost@example.com")
        self.assertEqual(
            str(ctx.exception), "ERROR: Hunter API 404 on /email-verifier."
        )

    def test_verify_email_network_error_raises(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.verify_email("alice@example.com")
        self.assertTrue(str(ctx.exception).startswith("ERROR: Hunter API request failed"))

    def test_verify_email_not_connected_raises(self):
        self.conn.disconnect()
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.verify_email("alice@example.com")
        self.assertTrue(str(ctx.exception).startswith("ERROR: Hunter is not connected"))


if __name__ == "__main__":
    unittest.main()
