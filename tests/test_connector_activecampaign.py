"""Tests for the ActiveCampaign connector (API key + account base URL). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import activecampaign as activecampaign_mod
from zeline.connectors.activecampaign import ActiveCampaignConnector

BASE_URL = "https://xxx.api-us1.com"
TOKEN = "tok_live_abc123"


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

    store.save("activecampaign", {"api_key": TOKEN, "base_url": BASE_URL})


class ActiveCampaignConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ac-test-"))
        _patch_store(self, self.tmp)
        self.conn = ActiveCampaignConnector()

    def test_connect_success_saves_credentials(self):
        from zeline.connectors import store

        payload = {"user": {"username": "jdoe", "email": "jdoe@example.com"}}
        with mock.patch(
            "requests.get", return_value=FakeResponse(payload)
        ) as get:
            result = self.conn.connect(TOKEN, BASE_URL)
        self.assertEqual(result, "Connected to ActiveCampaign (user jdoe).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/api/3/users/me")
        self.assertEqual(kwargs["headers"]["Api-Token"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("activecampaign"), {"api_key": TOKEN, "base_url": BASE_URL})

    def test_connect_token_and_base_url_kwargs(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"user": {"username": "jane"}})
        ):
            result = self.conn.connect(token=TOKEN, base_url=BASE_URL)
        self.assertEqual(result, "Connected to ActiveCampaign (user jane).")

    def test_connect_base_url_trailing_slash_stripped(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse({"user": {"username": "u"}})
        ) as get:
            result = self.conn.connect(TOKEN, f"{BASE_URL}/")
        self.assertTrue(result.startswith("Connected to ActiveCampaign"))
        args, _ = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/api/3/users/me")
        self.assertEqual(store.load("activecampaign")["base_url"], BASE_URL)

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", BASE_URL)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("activecampaign"))
        get.assert_not_called()

    def test_connect_missing_base_url_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("activecampaign"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN, BASE_URL)
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("activecampaign"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token", BASE_URL)
        self.assertTrue(result.startswith("ERROR: ActiveCampaign rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("activecampaign"))

    def test_status_connected_has_no_secret(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(),
            {"connected": True, "detail": f"linked to {BASE_URL}"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "ActiveCampaign disconnected.")
        self.assertEqual(self.conn.disconnect(), "ActiveCampaign was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "activecampaign")
        self.assertEqual(self.conn.name, "ActiveCampaign")
        self.assertEqual(self.conn.description, "Read and create ActiveCampaign contacts.")
        self.assertEqual(self.conn.auth_kind, "pat")


class ActiveCampaignOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ac-test-"))
        _patch_store(self, self.tmp)
        self.conn = ActiveCampaignConnector()
        _seed_connected()

    def _patch_request(self, payload, status=200):
        return mock.patch(
            "requests.request", return_value=FakeResponse(payload, status)
        )

    def test_list_contacts(self):
        payload = {
            "contacts": [
                {"id": "1", "email": "alice@example.com"},
                {"id": "2", "email": "bob@example.com"},
            ]
        }
        with self._patch_request(payload) as req:
            result = self.conn.list_contacts(limit=2)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE_URL}/api/3/contacts")
        self.assertEqual(kwargs["params"]["limit"], 2)
        self.assertEqual(kwargs["headers"]["Api-Token"], TOKEN)
        self.assertIn("1: alice@example.com", result)
        self.assertIn("2: bob@example.com", result)

    def test_list_contacts_limit_clamped(self):
        with self._patch_request({"contacts": []}) as req:
            self.conn.list_contacts(limit=999)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)

    def test_create_contact(self):
        payload = {
            "contact": {"id": "42", "email": "new@example.com", "firstName": "New", "lastName": "Guy"}
        }
        with self._patch_request(payload) as req:
            result = self.conn.create_contact("new@example.com", "New", "Guy")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE_URL}/api/3/contacts")
        self.assertEqual(
            kwargs["json"],
            {"contact": {"email": "new@example.com", "firstName": "New", "lastName": "Guy"}},
        )
        self.assertEqual(result, "Contact created: 42 (new@example.com).")

    def test_create_contact_requires_email(self):
        with self.assertRaisesRegex(RuntimeError, r"^ERROR:"):
            self.conn.create_contact("   ")

    def test_operation_http_error_raises(self):
        with self._patch_request({"error": "boom"}, status=500):
            with self.assertRaisesRegex(RuntimeError, r"ERROR: ActiveCampaign API 500"):
                self.conn.list_contacts()
        with self._patch_request({"error": "boom"}, status=500):
            with self.assertRaisesRegex(RuntimeError, r"ERROR: ActiveCampaign API 500"):
                self.conn.create_contact("x@example.com")

    def test_operation_network_error_raises(self):
        import requests

        with mock.patch(
            "requests.request", side_effect=requests.Timeout("slow")
        ), self.assertRaisesRegex(RuntimeError, r"^ERROR:"):
            self.conn.list_contacts()

    def test_operation_when_disconnected_raises(self):
        self.conn.disconnect()
        with self.assertRaisesRegex(RuntimeError, r"^ERROR:"):
            self.conn.list_contacts()
        with self.assertRaisesRegex(RuntimeError, r"^ERROR:"):
            self.conn.create_contact("x@example.com")


if __name__ == "__main__":
    unittest.main()
