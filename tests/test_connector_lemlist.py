"""Tests for the lemlist connector (API key, Basic auth). All HTTP is mocked."""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import lemlist as lemlist_mod
from zeline.connectors.lemlist import LemlistConnector

API_KEY = "lemlist_fake_api_key_123"
EXPECTED_AUTH = f"Basic {base64.b64encode(f':{API_KEY}'.encode()).decode()}"
CAMPAIGNS = [
    {"_id": "c1", "name": "Outreach Q3", "status": "active"},
    {"_id": "c2", "name": "Cold Leads"},
]
STATS = {
    "_id": "c1",
    "name": "Outreach Q3",
    "sent": 1200,
    "opened": 480,
    "clicked": 96,
    "replied": 42,
    "bounced": 18,
    "unsubscribed": 7,
    "leads": 1300,
}


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

    store.save("lemlist", {"api_key": API_KEY})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class LemlistConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-lemlist-test-"))
        _patch_store(self, self.tmp)
        self.conn = LemlistConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse(list(CAMPAIGNS))
        ) as get:
            result = self.conn.connect(API_KEY)
        self.assertEqual(result, "Connected to lemlist (2 campaigns).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.lemlist.com/api/campaigns")
        self.assertEqual(kwargs["headers"]["Authorization"], EXPECTED_AUTH)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("lemlist"), {"api_key": API_KEY})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse([])):
            result = self.conn.connect(token=API_KEY)
        self.assertEqual(result, "Connected to lemlist (0 campaigns).")

    def test_connect_missing_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR: no API key"))
        self.assertIsNone(store.load("lemlist"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch(
            "zeline.connectors.store.save"
        ) as save, mock.patch(
            "requests.get", return_value=FakeResponse({}, status=401)
        ):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: lemlist rejected the API key"))
        self.assertIn("401", result)
        save.assert_not_called()
        self.assertIsNone(store.load("lemlist"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(API_KEY)
        self.assertTrue(result.startswith("ERROR: could not reach the lemlist API"))
        self.assertIsNone(store.load("lemlist"))

    def test_connect_non_json_200_still_connects(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(API_KEY)
        self.assertEqual(result, "Connected to lemlist (0 campaigns).")
        self.assertEqual(store.load("lemlist"), {"api_key": API_KEY})

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "linked"})

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Lemlist disconnected.")
        self.assertEqual(self.conn.disconnect(), "Lemlist was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "lemlist")
        self.assertEqual(self.conn.name, "Lemlist")
        self.assertEqual(self.conn.description, "List campaigns and view campaign stats.")
        self.assertEqual(self.conn.auth_kind, "pat")


class LemlistOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-lemlist-test-"))
        _patch_store(self, self.tmp)
        self.conn = LemlistConnector()
        _seed_connected()

    def test_list_campaigns(self):
        mapping = {
            ("GET", "https://api.lemlist.com/api/campaigns"): (list(CAMPAIGNS), 200),
        }
        with mock.patch(
            "requests.request", side_effect=_request_side_effect(mapping)
        ) as req:
            result = self.conn.list_campaigns(limit=2)
        self.assertEqual(result, "c1: Outreach Q3 (active)\nc2: Cold Leads")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.lemlist.com/api/campaigns")
        self.assertEqual(kwargs["headers"]["Authorization"], EXPECTED_AUTH)
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_campaigns_dict_envelope(self):
        mapping = {
            ("GET", "https://api.lemlist.com/api/campaigns"): (
                {"campaigns": CAMPAIGNS[:1]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_campaigns()
        self.assertEqual(result, "c1: Outreach Q3 (active)")

    def test_list_campaigns_limit_clamped(self):
        mapping = {
            ("GET", "https://api.lemlist.com/api/campaigns"): (
                [{"_id": f"c{i}", "name": f"Camp {i}"} for i in range(100)],
                200,
            ),
        }
        with mock.patch(
            "requests.request", side_effect=_request_side_effect(mapping)
        ) as req:
            result = self.conn.list_campaigns(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_campaigns_min_limit(self):
        mapping = {("GET", "https://api.lemlist.com/api/campaigns"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_campaigns(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 1})

    def test_list_campaigns_empty(self):
        mapping = {("GET", "https://api.lemlist.com/api/campaigns"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_campaigns(), "No campaigns found.")

    def test_list_campaigns_500_raises(self):
        mapping = {("GET", "https://api.lemlist.com/api/campaigns"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_campaigns()
        self.assertEqual(
            str(ctx.exception), "ERROR: lemlist API 500 on /campaigns."
        )

    def test_list_campaigns_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_campaigns()
        self.assertIn("ERROR: lemlist API request failed", str(ctx.exception))

    def test_list_campaigns_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_campaigns()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_campaign_stats(self):
        mapping = {
            ("GET", "https://api.lemlist.com/api/campaigns/c1"): (dict(STATS), 200),
        }
        with mock.patch(
            "requests.request", side_effect=_request_side_effect(mapping)
        ) as req:
            result = self.conn.campaign_stats("c1")
        self.assertEqual(
            result,
            "Outreach Q3 (c1): sent: 1200, opened: 480, clicked: 96, replied: 42, "
            "bounced: 18, unsubscribed: 7, leads: 1300",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.lemlist.com/api/campaigns/c1")
        self.assertEqual(kwargs["headers"]["Authorization"], EXPECTED_AUTH)

    def test_campaign_stats_no_stats_available(self):
        mapping = {
            ("GET", "https://api.lemlist.com/api/campaigns/c2"): (
                {"_id": "c2", "name": "Cold Leads"},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.campaign_stats("c2")
        self.assertEqual(result, "Cold Leads (c2): no stats available")

    def test_campaign_stats_404_raises(self):
        mapping = {("GET", "https://api.lemlist.com/api/campaigns/nope"): ({}, 404)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.campaign_stats("nope")
        self.assertIn("ERROR: lemlist API 404", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("lemlist")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_campaigns()
        self.assertIn("zeline connect lemlist", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.campaign_stats("c1")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(API_KEY, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_campaigns()
        self.assertNotIn(API_KEY, str(ctx.exception))


class LemlistRegistryTests(unittest.TestCase):
    def test_lemlist_registered(self):
        from zeline.connectors import get

        conn = get("lemlist")
        self.assertIsInstance(conn, LemlistConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(lemlist_mod.LemlistConnector.id, "lemlist")


if __name__ == "__main__":
    unittest.main()
