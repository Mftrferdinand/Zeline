"""Tests for the Zendesk connector. All HTTP is mocked; no real network or tokens."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.zendesk import ZendeskConnector, _normalize_subdomain


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

    store.save(
        "zendesk",
        {"email": "agent@acme.com", "token": "zd-secret-token", "subdomain": "acme"},
    )


class ZendeskConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-zendesk-test-"))
        _patch_store(self, self.tmp)
        self.conn = ZendeskConnector()

    def test_normalize_subdomain(self):
        self.assertEqual(_normalize_subdomain(" Acme "), "acme")
        self.assertEqual(_normalize_subdomain("ACME.ZENDESK.COM"), "acme")
        self.assertEqual(_normalize_subdomain("acme.zendesk.com"), "acme")

    def test_connect_missing_fields(self):
        cases = [
            {"email": "", "token": "t", "subdomain": "acme"},
            {"email": "a@b.c", "token": "", "subdomain": "acme"},
            {"email": "a@b.c", "token": "t", "subdomain": ""},
        ]
        for kwargs in cases:
            with mock.patch("zeline.connectors.store.save") as save:
                self.assertTrue(self.conn.connect(**kwargs).startswith("ERROR:"))
                save.assert_not_called()

    def test_connect_bad_credentials_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "Couldn't authenticate you"}, status=401)
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(
                email="agent@acme.com", token="bad", subdomain="ACME.ZENDESK.COM "
            )
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/api/v2/users/me.json"))
        self.assertTrue(args[0].startswith("https://acme.zendesk.com"))
        self.assertEqual(kwargs["auth"], ("agent@acme.com/token", "bad"))
        self.assertIsNone(store.load("zendesk"))

    def test_connect_network_error(self):
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(
                email="agent@acme.com", token="zd-secret-token", subdomain="acme"
            )
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("could not reach", result)
        self.assertIsNone(store.load("zendesk"))

    def test_connect_success_saves_credentials(self):
        fake = FakeResponse({"user": {"id": 1, "name": "Jane Agent"}})
        with mock.patch("requests.get", return_value=fake), mock.patch(
            "zeline.connectors.store.save"
        ) as save:
            result = self.conn.connect(
                email="agent@acme.com", token="zd-secret-token", subdomain="acme"
            )
        self.assertEqual(result, "Connected to Zendesk (acme) as Jane Agent.")
        self.assertNotIn("zd-secret-token", result)
        save.assert_called_once_with(
            "zendesk",
            {"email": "agent@acme.com", "token": "zd-secret-token", "subdomain": "acme"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})
        self.assertFalse(self.conn.is_connected())

    def test_status_connected_never_leaks_secret(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "acme")
        self.assertNotIn("zd-secret-token", status["detail"])
        self.assertNotIn("agent@acme.com", status["detail"])
        self.assertTrue(self.conn.is_connected())

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Zendesk disconnected.")
        self.assertEqual(self.conn.disconnect(), "Zendesk was not connected.")


class ZendeskOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-zendesk-test-"))
        _patch_store(self, self.tmp)
        self.conn = ZendeskConnector()
        _seed_connected(self.tmp)

    def test_list_tickets(self):
        fake = FakeResponse(
            {
                "tickets": [
                    {"id": 5, "subject": "Login issue", "status": "open"},
                    {"id": 6, "subject": "Refund", "status": "pending"},
                ]
            }
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_tickets(limit=10)
        self.assertEqual(result, "#5 Login issue [open]\n#6 Refund [pending]")
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/api/v2/tickets.json"))
        self.assertTrue(args[1].startswith("https://acme.zendesk.com"))
        self.assertEqual(kwargs["auth"], ("agent@acme.com/token", "zd-secret-token"))
        self.assertEqual(kwargs["params"], {"per_page": 10})

    def test_list_tickets_limit_clamped(self):
        fake = FakeResponse({"tickets": []})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.list_tickets(limit=500)
            self.assertEqual(req.call_args[1]["params"]["per_page"], 100)
            self.conn.list_tickets(limit=0)
            self.assertEqual(req.call_args[1]["params"]["per_page"], 1)

    def test_list_tickets_empty(self):
        fake = FakeResponse({"tickets": []})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.list_tickets(), "No tickets found.")

    def test_create_ticket(self):
        fake = FakeResponse({"ticket": {"id": 42, "subject": "Help me"}})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_ticket("Help me", "Please assist", "urgent")
        self.assertEqual(result, "Created ticket #42.")
        self.assertNotIn("zd-secret-token", result)
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/api/v2/tickets.json"))
        self.assertEqual(
            kwargs["json"],
            {"ticket": {"subject": "Help me", "comment": {"body": "Please assist"}, "priority": "urgent"}},
        )

    def test_create_ticket_empty_fields(self):
        self.assertTrue(self.conn.create_ticket("", "body").startswith("ERROR:"))
        self.assertTrue(self.conn.create_ticket("subj", "").startswith("ERROR:"))
        self.assertTrue(self.conn.create_ticket("subj", None).startswith("ERROR:"))


class ZendeskErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-zendesk-test-"))
        _patch_store(self, self.tmp)
        self.conn = ZendeskConnector()
        _seed_connected(self.tmp)

    def test_operation_request_exception(self):
        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Zendesk API request failed"):
                self.conn.list_tickets()

    def test_operation_http_403(self):
        fake = FakeResponse({"error": "forbidden"}, status=403)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Zendesk API 403"):
                self.conn.list_tickets()
            with self.assertRaisesRegex(RuntimeError, r"^ERROR: Zendesk API 403"):
                self.conn.create_ticket("s", "c")

    def test_operation_without_connect(self):
        from zeline.connectors import store

        store.delete("zendesk")
        with self.assertRaisesRegex(RuntimeError, r"not connected"):
            self.conn.list_tickets()


if __name__ == "__main__":
    unittest.main()
