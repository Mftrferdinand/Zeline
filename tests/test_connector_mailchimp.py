"""Tests for the Mailchimp connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import mailchimp as mailchimp_mod
from zeline.connectors.mailchimp import MailchimpConnector

DC = "us19"
API_BASE = f"https://{DC}.api.mailchimp.com/3.0"
KEY = "ZZZZ_TEST_KEY_NOT_REAL_XXXXXXXXXXXXXXXX-us19"
NO_SUFFIX_KEY = "keywithoutsuffix"


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

    store.save("mailchimp", {"api_key": KEY, "datacenter": DC})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class MailchimpConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-mc-test-"))
        _patch_store(self, self.tmp)
        self.conn = MailchimpConnector()

    def test_connect_success_saves_key_and_dc(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"account_name": "Acme", "account_id": "a1"}),
        ) as get:
            result = self.conn.connect(KEY)
        self.assertEqual(result, "Connected to Mailchimp (account Acme).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/")
        self.assertEqual(kwargs["auth"], ("anystring", KEY))
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("mailchimp"), {"api_key": KEY, "datacenter": DC})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"account_name": "Acme"})
        ):
            result = self.conn.connect(token=KEY)
        self.assertEqual(result, "Connected to Mailchimp (account Acme).")

    def test_connect_datacenter_from_kwarg(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"account_name": "Acme"}),
        ) as get:
            result = self.conn.connect(NO_SUFFIX_KEY, datacenter=DC)
        self.assertEqual(result, "Connected to Mailchimp (account Acme).")
        self.assertEqual(get.call_args.args[0], f"{API_BASE}/")
        self.assertEqual(store.load("mailchimp")["datacenter"], DC)

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("mailchimp"))
        get.assert_not_called()

    def test_connect_missing_datacenter_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(NO_SUFFIX_KEY)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("datacenter required", result)
        self.assertIsNone(store.load("mailchimp"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(KEY)
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIn(f"{DC}.api.mailchimp.com", result)
        self.assertIsNone(store.load("mailchimp"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token-us19")
        self.assertTrue(result.startswith("ERROR: Mailchimp rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("mailchimp"))

    def test_connect_non_json_body_does_not_crash(self):
        resp = FakeResponse("<html>ok</html>", status=200)
        resp.json = mock.Mock(side_effect=ValueError("no json"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(KEY)
        self.assertEqual(result, "Connected to Mailchimp (account ?).")

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Mailchimp disconnected.")
        self.assertEqual(self.conn.disconnect(), "Mailchimp was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "mailchimp")
        self.assertEqual(self.conn.name, "Mailchimp")
        self.assertEqual(self.conn.auth_kind, "pat")


class MailchimpOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-mc-test-"))
        _patch_store(self, self.tmp)
        self.conn = MailchimpConnector()
        _seed_connected()

    def test_list_audiences(self):
        mapping = {
            ("GET", f"{API_BASE}/lists"): (
                {"lists": [
                    {"id": "a1", "name": "Newsletter", "stats": {"member_count": 120}},
                    {"id": "a2", "name": "VIP", "stats": {"member_count": 5}},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_audiences(limit=2)
        self.assertEqual(result, "Newsletter (120 members)\nVIP (5 members)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{API_BASE}/lists")
        self.assertEqual(kwargs["auth"], ("anystring", KEY))
        self.assertEqual(kwargs["params"], {"count": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_audiences_limit_clamped(self):
        payload = {
            "lists": [
                {"id": f"a{i}", "name": f"A{i}", "stats": {"member_count": i}}
                for i in range(100)
            ]
        }
        mapping = {("GET", f"{API_BASE}/lists"): (payload, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_audiences(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"count": 100})

    def test_list_audiences_min_limit(self):
        mapping = {("GET", f"{API_BASE}/lists"): ({"lists": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_audiences(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"count": 1})

    def test_list_audiences_empty(self):
        mapping = {("GET", f"{API_BASE}/lists"): ({"lists": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_audiences(), "No audiences found.")

    def test_list_audiences_http_error(self):
        mapping = {("GET", f"{API_BASE}/lists"): ({}, 403)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_audiences()
        self.assertEqual(str(ctx.exception), "ERROR: Mailchimp API 403 on /lists.")

    def test_list_audiences_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_audiences()
        self.assertIn("ERROR: Mailchimp API request failed", str(ctx.exception))

    def test_list_audiences_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_audiences()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_list_campaigns(self):
        mapping = {
            ("GET", f"{API_BASE}/campaigns"): (
                {"campaigns": [
                    {"id": "c1", "status": "sent", "settings": {"title": "October Blast"}},
                    {"id": "c2", "status": "save", "settings": {"title": "Draft One"}},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_campaigns(limit=2)
        self.assertEqual(result, "October Blast [sent]\nDraft One [save]")
        args, kwargs = req.call_args
        self.assertEqual(args[1], f"{API_BASE}/campaigns")
        self.assertEqual(kwargs["auth"], ("anystring", KEY))
        self.assertEqual(kwargs["params"], {"count": 2})

    def test_list_campaigns_empty(self):
        mapping = {("GET", f"{API_BASE}/campaigns"): ({"campaigns": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_campaigns(), "No campaigns found.")

    def test_list_campaigns_server_error(self):
        mapping = {("GET", f"{API_BASE}/campaigns"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_campaigns()
        self.assertEqual(str(ctx.exception), "ERROR: Mailchimp API 500 on /campaigns.")

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("mailchimp")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_audiences()
        self.assertIn("zeline connect mailchimp", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_campaigns()

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(KEY, str(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_audiences()
        self.assertNotIn(KEY, str(ctx.exception))


class MailchimpRegistryTests(unittest.TestCase):
    def test_mailchimp_registered(self):
        from zeline.connectors import get

        conn = get("mailchimp")
        self.assertIsInstance(conn, MailchimpConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(mailchimp_mod.MailchimpConnector.id, "mailchimp")


if __name__ == "__main__":
    unittest.main()
