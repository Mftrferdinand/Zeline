"""Tests for the Freshdesk connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from requests.auth import HTTPBasicAuth

from zeline.connectors import freshdesk as freshdesk_mod
from zeline.connectors.freshdesk import FreshdeskConnector

SUB = "acme"


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

    store.save("freshdesk", {"api_key": "k1", "subdomain": SUB, "name": "Agent A"})


class FreshdeskConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-fd-test-"))
        _patch_store(self, self.tmp)
        self.conn = FreshdeskConnector()

    def test_connect_success_saves_creds(self):
        from zeline.connectors import store

        body = {"contact": {"name": "Agent A"}}
        with mock.patch("requests.get", return_value=FakeResponse(body)) as get:
            result = self.conn.connect("k1", "acme")
        self.assertEqual(result, "Connected to Freshdesk as Agent A.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"https://{SUB}.freshdesk.com/api/v2/agents/me")
        auth = kwargs["auth"]
        self.assertIsInstance(auth, HTTPBasicAuth)
        self.assertEqual(auth.username, "k1")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("freshdesk")
        self.assertEqual(saved["api_key"], "k1")
        self.assertEqual(saved["subdomain"], "acme")

    def test_connect_subdomain_normalized(self):
        with mock.patch("requests.get", return_value=FakeResponse({"contact": {"name": "A"}})) as get:
            self.conn.connect("k1", "https://Acme/")
        args, _ = get.call_args
        self.assertTrue(args[0].startswith("https://acme.freshdesk.com/"))

    def test_connect_missing_params(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect("", "acme").startswith("ERROR: api_key and subdomain"))
        self.assertTrue(self.conn.connect("k1", "").startswith("ERROR: api_key and subdomain"))
        self.assertIsNone(store.load("freshdesk"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect("k1", "acme")
        self.assertTrue(result.startswith("ERROR: could not reach Freshdesk"))
        self.assertIsNone(store.load("freshdesk"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("k1", "acme")
        self.assertIn("401", result)
        self.assertIsNone(store.load("freshdesk"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(), {"connected": True, "detail": "acme (Agent A)"}
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Freshdesk disconnected.")
        self.assertEqual(self.conn.disconnect(), "Freshdesk was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "freshdesk")
        self.assertEqual(self.conn.name, "Freshdesk")
        self.assertEqual(self.conn.auth_kind, "pat")


class FreshdeskOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-fd-test-"))
        _patch_store(self, self.tmp)
        self.conn = FreshdeskConnector()
        _seed_connected()

    def _request_side_effect(self, mapping):
        def _side_effect(method, url, *args, **kwargs):
            key = (method, url)
            if key in mapping:
                payload, status = mapping[key]
                return FakeResponse(payload, status)
            raise AssertionError(f"unexpected {method} {url}")

        return _side_effect

    def test_list_tickets(self):
        mapping = {
            ("GET", f"https://{SUB}.freshdesk.com/api/v2/tickets"): (
                [
                    {"id": 11, "subject": "Login broken", "status": 2},
                    {"id": 12, "subject": "Refund", "status": 5},
                ],
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_tickets(limit=2)
        self.assertEqual(result, "#11: Login broken [Open]\n#12: Refund [Closed]")
        _, kwargs = req.call_args
        self.assertEqual(kwargs["params"], {"per_page": 2})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["auth"].username, "k1")

    def test_list_tickets_unknown_status_code(self):
        mapping = {
            ("GET", f"https://{SUB}.freshdesk.com/api/v2/tickets"): (
                [{"id": 1, "subject": "X", "status": 99}],
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)):
            self.assertEqual(self.conn.list_tickets(), "#1: X [99]")

    def test_list_tickets_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_tickets(), "No tickets found.")

    def test_list_tickets_limit_clamped(self):
        mapping = {
            ("GET", f"https://{SUB}.freshdesk.com/api/v2/tickets"): (
                [{"id": i, "subject": "S", "status": 2} for i in range(150)],
                200,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.list_tickets(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"per_page": 100})

    def test_list_tickets_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tickets()
        self.assertIn("ERROR: Freshdesk API 500", str(ctx.exception))

    def test_list_tickets_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tickets()
        self.assertIn("ERROR: Freshdesk API request failed", str(ctx.exception))

    def test_create_ticket(self):
        mapping = {
            ("POST", f"https://{SUB}.freshdesk.com/api/v2/tickets"): (
                {"id": 77, "subject": "Hi"},
                201,
            )
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            result = self.conn.create_ticket(
                "Hi", "help please", email="u@e.com", priority=2, status=2
            )
        self.assertEqual(result, "#77 created")
        body = req.call_args.kwargs["json"]
        self.assertEqual(body["subject"], "Hi")
        self.assertEqual(body["description"], "help please")
        self.assertEqual(body["email"], "u@e.com")
        self.assertEqual(body["priority"], 2)
        self.assertEqual(body["status"], 2)

    def test_create_ticket_defaults(self):
        mapping = {
            ("POST", f"https://{SUB}.freshdesk.com/api/v2/tickets"): ({"id": 78}, 201)
        }
        with mock.patch("requests.request", side_effect=self._request_side_effect(mapping)) as req:
            self.assertEqual(self.conn.create_ticket("S", "D"), "#78 created")
        body = req.call_args.kwargs["json"]
        self.assertEqual(body["priority"], 1)
        self.assertEqual(body["status"], 2)
        self.assertNotIn("email", body)

    def test_create_ticket_missing_fields(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_ticket("", "D")
        self.assertIn("subject and description", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("freshdesk")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_tickets()
        self.assertIn("zeline connect freshdesk", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_ticket("S", "D")


class FreshdeskRegistryTests(unittest.TestCase):
    def test_registered(self):
        from zeline.connectors import get

        self.assertIsInstance(get("freshdesk"), FreshdeskConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(freshdesk_mod.FreshdeskConnector.id, "freshdesk")


if __name__ == "__main__":
    unittest.main()
