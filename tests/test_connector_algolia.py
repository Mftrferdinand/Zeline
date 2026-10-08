"""Tests for the Algolia connector (application ID + API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import algolia as algolia_mod
from zeline.connectors.algolia import AlgoliaConnector

APP_ID = "myappid"
API_KEY = "alg_fake_key_123"
BASE = f"https://{APP_ID}-dsn.algolia.net/1"


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

    store.save("algolia", {"app_id": APP_ID, "api_key": API_KEY})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class AlgoliaConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-alg-test-"))
        _patch_store(self, self.tmp)
        self.conn = AlgoliaConnector()

    def test_connect_success_saves_credentials(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"items": [{"name": "a"}, {"name": "b"}]}),
        ) as get:
            result = self.conn.connect(APP_ID, API_KEY)
        self.assertEqual(result, f"Connected to Algolia (app {APP_ID}, 2 indices).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/indexes")
        self.assertEqual(kwargs["headers"]["X-Algolia-Application-Id"], APP_ID)
        self.assertEqual(kwargs["headers"]["X-Algolia-API-Key"], API_KEY)
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("algolia"), {"app_id": APP_ID, "api_key": API_KEY})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"items": []})
        ):
            result = self.conn.connect(APP_ID, token=API_KEY)
        self.assertEqual(result, f"Connected to Algolia (app {APP_ID}, 0 indices).")

    def test_connect_app_id_kwarg(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"items": []})
        ) as get:
            result = self.conn.connect(app_id=APP_ID, api_key=API_KEY)
        self.assertTrue(result.startswith("Connected to Algolia"))
        self.assertEqual(get.call_args.args[0], f"{BASE}/indexes")

    def test_connect_200_non_json_does_not_crash(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(APP_ID, API_KEY)
        self.assertTrue(result.startswith("Connected to Algolia"))
        self.assertEqual(store.load("algolia"), {"app_id": APP_ID, "api_key": API_KEY})

    def test_connect_empty_app_id_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", API_KEY)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("algolia"))
        get.assert_not_called()

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(APP_ID, "")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("algolia"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(APP_ID, "bogus-key")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertNotIn("bogus-key", result)
        self.assertIsNone(store.load("algolia"))

    def test_connect_request_exception_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(APP_ID, API_KEY)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("algolia"))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Algolia disconnected.")
        self.assertEqual(self.conn.disconnect(), "Algolia was not connected.")

    def test_status_connected(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertIn(APP_ID, status["detail"])
        self.assertNotIn(API_KEY, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "algolia")
        self.assertEqual(self.conn.name, "Algolia")
        self.assertEqual(self.conn.auth_kind, "pat")
        self.assertIn("indices", self.conn.description)


class AlgoliaOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-alg-test-"))
        _patch_store(self, self.tmp)
        self.conn = AlgoliaConnector()
        _seed_connected()

    def test_list_indexes(self):
        mapping = {
            ("GET", f"{BASE}/indexes"): (
                {"items": [{"name": "products"}, {"name": "docs"}]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_indexes()
        self.assertEqual(result, "products\ndocs")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/indexes")
        self.assertEqual(kwargs["headers"]["X-Algolia-Application-Id"], APP_ID)
        self.assertEqual(kwargs["headers"]["X-Algolia-API-Key"], API_KEY)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_indexes_empty(self):
        mapping = {("GET", f"{BASE}/indexes"): ({"items": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_indexes(), "No indices found.")

    def test_search_index(self):
        mapping = {
            ("POST", f"{BASE}/indexes/products/query"): (
                {"hits": [
                    {"objectID": "1", "title": "Foo Widget"},
                    {"objectID": "2"},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.search_index("products", "widget", limit=2)
        self.assertEqual(result, "1: title=Foo Widget\n2")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE}/indexes/products/query")
        self.assertEqual(
            kwargs["json"],
            {"params": "query=widget&hitsPerPage=2"},
        )
        self.assertEqual(kwargs["timeout"], 30)

    def test_search_index_empty(self):
        mapping = {
            ("POST", f"{BASE}/indexes/products/query"): ({"hits": []}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(
                self.conn.search_index("products", "zzz"),
                "No hits for 'zzz' in index products.",
            )

    def test_search_index_limit_clamped(self):
        mapping = {
            ("POST", f"{BASE}/indexes/products/query"): ({"hits": []}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.search_index("products", "x", limit=500)
        self.assertEqual(req.call_args.kwargs["json"], {"params": "query=x&hitsPerPage=100"})
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.search_index("products", "x", limit=0)
        self.assertEqual(req.call_args.kwargs["json"], {"params": "query=x&hitsPerPage=1"})

    def test_operation_http_error(self):
        mapping = {("GET", f"{BASE}/indexes"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_indexes()
        self.assertEqual(str(ctx.exception), "ERROR: Algolia API 500 on /indexes.")

    def test_operation_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_indexes()
        self.assertIn("ERROR: Algolia API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("algolia")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_indexes()
        self.assertIn("zeline connect algolia", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search_index("products", "x")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(API_KEY, str(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_indexes()
        self.assertNotIn(API_KEY, str(ctx.exception))


class AlgoliaRegistryTests(unittest.TestCase):
    def test_algolia_registered(self):
        from zeline.connectors import get

        conn = get("algolia")
        self.assertIsInstance(conn, AlgoliaConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(algolia_mod.AlgoliaConnector.id, "algolia")


if __name__ == "__main__":
    unittest.main()
