"""Tests for the Confluence connector (email + API token, HTTP Basic auth). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.confluence import ConfluenceConnector

BASE = "https://acme.atlassian.net"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save(
        "confluence",
        {
            "email": "ops@acme.test",
            "token": "SECRET-TOKEN",
            "base_url": BASE,
            "user": "Ops Bot",
        },
    )


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-confluence-test-"))
        _patch_store(self, self.tmp)
        self.conn = ConfluenceConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"displayName": "Ops Bot"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(
                email="ops@acme.test", token="SECRET-TOKEN", base_url=BASE
            )
        self.assertEqual(result, "Connected to Confluence as Ops Bot.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/wiki/rest/api/user/current")
        self.assertEqual(kwargs["auth"], ("ops@acme.test", "SECRET-TOKEN"))
        saved = store.load("confluence")
        self.assertEqual(saved["email"], "ops@acme.test")
        self.assertEqual(saved["token"], "SECRET-TOKEN")
        self.assertEqual(saved["base_url"], BASE)
        self.assertEqual(saved["user"], "Ops Bot")

    def test_connect_strips_trailing_slash(self):
        fake = FakeResponse({"displayName": "Ops Bot"})
        with mock.patch("requests.get", return_value=fake) as get:
            self.conn.connect(email="e@x.t", token="T", base_url=BASE + "/// ")
        self.assertEqual(get.call_args[0][0], f"{BASE}/wiki/rest/api/user/current")

    def test_connect_missing_params(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(email="e@x.t").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(email="e@x.t", token="T").startswith("ERROR:"))

    def test_connect_rejects_non_https_base_url(self):
        for bad in ("http://acme.atlassian.net", "acme.atlassian.net", "ftp://x.y"):
            result = self.conn.connect(email="e@x.t", token="T", base_url=bad)
            self.assertTrue(result.startswith("ERROR:"), bad)

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(email="e@x.t", token="BAD", base_url=BASE)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("confluence"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(email="e@x.t", token="T", base_url=BASE)
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "Ops Bot")
        self.assertNotIn("SECRET-TOKEN", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Confluence disconnected.")
        self.assertEqual(self.conn.disconnect(), "Confluence was not connected.")


class SearchPagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-confluence-test-"))
        _patch_store(self, self.tmp)
        self.conn = ConfluenceConnector()
        _seed_connected(self.tmp)

    def _results(self, n=2):
        return {
            "results": [
                {"id": str(1000 + i), "title": f"page title {i}"}
                for i in range(1, n + 1)
            ]
        }

    def test_search_pages_formats(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._results())) as req:
            result = self.conn.search_pages("type = page")
        self.assertEqual(result, "1001: page title 1\n1002: page title 2")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/wiki/rest/api/content/search")
        self.assertEqual(kwargs["auth"], ("ops@acme.test", "SECRET-TOKEN"))
        self.assertEqual(kwargs["params"]["cql"], "type = page")
        self.assertEqual(kwargs["params"]["limit"], 10)

    def test_search_pages_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._results())) as req:
            self.conn.search_pages("x", limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse(self._results())) as req:
            self.conn.search_pages("x", limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_search_pages_empty_cql(self):
        self.assertTrue(self.conn.search_pages("").startswith("ERROR:"))
        self.assertTrue(self.conn.search_pages("   ").startswith("ERROR:"))

    def test_search_pages_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"results": []})):
            self.assertEqual(self.conn.search_pages("x"), "No pages found.")

    def test_search_pages_api_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=400)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_pages("x")
        self.assertIn("ERROR: Confluence API 400", str(ctx.exception))

    def test_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("confluence")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.search_pages("x")
        self.assertIn("zeline connect confluence", str(ctx.exception))


class GetPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-confluence-test-"))
        _patch_store(self, self.tmp)
        self.conn = ConfluenceConnector()
        _seed_connected(self.tmp)

    def test_get_page_returns_first_500_chars(self):
        body = "x" * 800
        fake = FakeResponse({"id": "1001", "body": {"storage": {"value": body}}})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.get_page("1001")
        self.assertEqual(result, "x" * 500)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/wiki/rest/api/content/1001")
        self.assertEqual(kwargs["params"]["expand"], "body.storage")

    def test_get_page_short_body_not_truncated(self):
        fake = FakeResponse({"id": "1001", "body": {"storage": {"value": "hello"}}})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.get_page("1001"), "hello")

    def test_get_page_no_content(self):
        fake = FakeResponse({"id": "1001", "body": {}})
        with mock.patch("requests.request", return_value=fake):
            self.assertEqual(self.conn.get_page("1001"), "ERROR: no content.")

    def test_get_page_empty_id(self):
        self.assertTrue(self.conn.get_page("").startswith("ERROR:"))
        self.assertTrue(self.conn.get_page("   ").startswith("ERROR:"))

    def test_get_page_api_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_page("1001")
        self.assertIn("ERROR: Confluence API 404", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
