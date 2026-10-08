"""Tests for the Zoho CRM connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import zoho_crm as zoho_mod
from zeline.connectors.zoho_crm import ZohoCrmConnector

API_BASE = "https://www.zohoapis.com"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("zoho_crm", {"access_token": "tok", "base_url": API_BASE, "login": "a@b.com"})


class ZohoCrmConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-zoho-test-"))
        _patch_store(self, self.tmp)
        self.conn = ZohoCrmConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        body = {"users": [{"email": "a@b.com", "first_name": "A"}]}
        with mock.patch("requests.get", return_value=FakeResponse(body)) as get:
            result = self.conn.connect("tok")
        self.assertEqual(result, "Connected to Zoho CRM as a@b.com.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/crm/v2/users?type=CurrentUser")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("zoho_crm")
        self.assertEqual(saved["access_token"], "tok")
        self.assertEqual(saved["base_url"], API_BASE)

    def test_connect_custom_base_url_regional(self):
        with mock.patch("requests.get", return_value=FakeResponse({"users": [{"email": "e@x.eu"}]})) as get:
            result = self.conn.connect("tok", base_url="https://www.zohoapis.eu")
        self.assertIn("e@x.eu", result)
        args, _ = get.call_args
        self.assertTrue(args[0].startswith("https://www.zohoapis.eu/crm/v2/users"))

    def test_connect_missing_token(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect("").startswith("ERROR: no access token"))
        self.assertIsNone(store.load("zoho_crm"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect("tok")
        self.assertTrue(result.startswith("ERROR: could not reach Zoho CRM"))
        self.assertIsNone(store.load("zoho_crm"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("tok")
        self.assertTrue(result.startswith("ERROR: Zoho CRM rejected the token"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("zoho_crm"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(), {"connected": True, "detail": "a@b.com"}
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Zoho CRM disconnected.")
        self.assertEqual(self.conn.disconnect(), "Zoho CRM was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "zoho_crm")
        self.assertEqual(self.conn.name, "Zoho CRM")
        self.assertEqual(self.conn.auth_kind, "pat")


class ZohoCrmOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-zoho-test-"))
        _patch_store(self, self.tmp)
        self.conn = ZohoCrmConnector()
        _seed_connected()

    def _request_side_effect(self, mapping):
        def _side_effect(method, url, *args, **kwargs):
            key = (method, url)
            if key in mapping:
                payload, status = mapping[key]
                return FakeResponse(payload, status)
            raise AssertionError(f"unexpected {method} {url}")

        return _side_effect

    def test_list_contacts(self):
        mapping = {
            ("GET", f"{API_BASE}/crm/v2/Contacts"): (
                {"data": [
                    {"id": "1", "First_Name": "Jane", "Last_Name": "Doe", "Email": "j@d.com"},
                    {"id": "2", "First_Name": "Bob", "Last_Name": "Smith", "Email": None},
                ]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_contacts(limit=2)
        self.assertEqual(result, "1: Jane Doe <j@d.com>\n2: Bob Smith <?>")
        _, kwargs = req.call_args
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

    def test_list_contacts_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"data": []})):
            self.assertEqual(self.conn.list_contacts(), "No contacts found.")

    def test_list_contacts_limit_clamped(self):
        mapping = {
            ("GET", f"{API_BASE}/crm/v2/Contacts"): (
                {"data": [{"id": str(i), "First_Name": "F", "Last_Name": "L"} for i in range(120)]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_contacts(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 100})

    def test_list_contacts_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=503)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_contacts()
        self.assertIn("ERROR: Zoho CRM API 503", str(ctx.exception))

    def test_list_contacts_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_contacts()
        self.assertIn("ERROR: Zoho CRM API request failed", str(ctx.exception))

    def test_create_contact(self):
        mapping = {
            ("POST", f"{API_BASE}/crm/v2/Contacts"): (
                {"data": [{"status": "success", "details": {"id": "999"}}]},
                201,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.create_contact("Jane", "Doe", email="j@d.com")
        self.assertEqual(result, "Contact created: 999")
        body = req.call_args.kwargs["json"]
        self.assertEqual(
            body["data"][0],
            {"First_Name": "Jane", "Last_Name": "Doe", "Email": "j@d.com"},
        )

    def test_create_contact_rejected_record(self):
        mapping = {
            ("POST", f"{API_BASE}/crm/v2/Contacts"): (
                {"data": [{"status": "error", "message": "duplicate"}]},
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_contact("Jane", "Doe")
        self.assertIn("ERROR: Zoho CRM rejected the contact", str(ctx.exception))

    def test_create_contact_missing_names(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_contact("", "Doe")
        self.assertIn("first_name and last_name", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("zoho_crm")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_contacts()
        self.assertIn("zeline connect zoho_crm", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_contact("A", "B")


class ZohoCrmRegistryTests(unittest.TestCase):
    def test_registered(self):
        from zeline.connectors import get

        self.assertIsInstance(get("zoho_crm"), ZohoCrmConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(zoho_mod.ZohoCrmConnector.id, "zoho_crm")


if __name__ == "__main__":
    unittest.main()
