"""Tests for the HubSpot connector. All HTTP is mocked; no real network or tokens."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.hubspot import HubSpotConnector


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


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save("hubspot", {"token": "pat-secret-token"})


class HubSpotConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hubspot-test-"))
        _patch_store(self, self.tmp)
        self.conn = HubSpotConnector()

    def test_connect_empty_token_errors(self):
        from zeline.connectors import store

        for bad in ("", "   ", None):
            with mock.patch("zeline.connectors.store.save") as save:
                self.assertTrue(self.conn.connect(token=bad).startswith("ERROR:"))
                save.assert_not_called()
        self.assertIsNone(store.load("hubspot"))

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"status": "error"}, status=401)
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="pat-bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/crm/v3/objects/contacts"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pat-bad")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertIsNone(store.load("hubspot"))

    def test_connect_network_error(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="pat-abc")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("could not reach", result)
        self.assertIsNone(store.load("hubspot"))

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        fake = FakeResponse({"results": []})
        with mock.patch("requests.get", return_value=fake), mock.patch(
            "zeline.connectors.store.save"
        ) as save:
            result = self.conn.connect(token="pat-secret-token")
        self.assertEqual(result, "Connected to HubSpot.")
        self.assertNotIn("pat-secret-token", result)
        save.assert_called_once_with("hubspot", {"token": "pat-secret-token"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_never_leaks_token(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("pat-secret-token", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "HubSpot disconnected.")
        self.assertEqual(self.conn.disconnect(), "HubSpot was not connected.")


class HubSpotOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hubspot-test-"))
        _patch_store(self, self.tmp)
        self.conn = HubSpotConnector()
        _seed_connected(self.tmp)

    def test_list_contacts(self):
        fake = FakeResponse(
            {
                "results": [
                    {
                        "id": "101",
                        "properties": {
                            "email": "jane@example.com",
                            "firstname": "Jane",
                            "lastname": "Doe",
                        },
                    },
                    {"id": "102", "properties": {"email": "bob@example.com"}},
                ]
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_contacts(limit=10)
        self.assertEqual(result, "jane@example.com (Jane Doe)\nbob@example.com")
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/crm/v3/objects/contacts"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pat-secret-token")
        self.assertEqual(kwargs["params"]["limit"], 10)
        self.assertIn("email", kwargs["params"]["properties"])

    def test_list_contacts_limit_clamped(self):
        fake = FakeResponse({"results": []})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.list_contacts(limit=500)
            self.assertEqual(req.call_args[1]["params"]["limit"], 100)
            self.conn.list_contacts(limit=0)
            self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_list_contacts_empty(self):
        fake = FakeResponse({"results": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_contacts(), "No contacts found.")

    def test_create_contact(self):
        fake = FakeResponse({"id": "201", "properties": {"email": "new@example.com"}})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_contact("new@example.com", "New", "User")
        self.assertEqual(result, "Created contact 201.")
        self.assertNotIn("pat-secret-token", result)
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/crm/v3/objects/contacts"))
        self.assertEqual(
            kwargs["json"],
            {"properties": {"email": "new@example.com", "firstname": "New", "lastname": "User"}},
        )

    def test_create_contact_empty_email(self):
        for bad in ("", "   ", None):
            self.assertTrue(self.conn.create_contact(bad).startswith("ERROR:"))


class HubSpotErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-hubspot-test-"))
        _patch_store(self, self.tmp)
        self.conn = HubSpotConnector()
        _seed_connected(self.tmp)

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: HubSpot API request failed"):
                self.conn.list_contacts()

    def test_operation_http_403(self):
        fake = FakeResponse({"status": "error"}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: HubSpot API 403"):
                self.conn.list_contacts()
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: HubSpot API 403"):
                self.conn.create_contact("new@example.com")


if __name__ == "__main__":
    unittest.main()
