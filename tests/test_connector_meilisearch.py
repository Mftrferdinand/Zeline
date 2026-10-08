"""Tests for the Meilisearch connector (master key + base URL). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import meilisearch as meilisearch_mod
from zeline.connectors.meilisearch import MeilisearchConnector

BASE_URL = "http://127.0.0.1:7700"
MASTER_KEY = "ms_fake_master_key_123"


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

    store.save("meilisearch", {"master_key": MASTER_KEY, "base_url": BASE_URL})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class MeilisearchConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ms-test-"))
        _patch_store(self, self.tmp)
        self.conn = MeilisearchConnector()

    def test_connect_success_saves_credentials(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"results": [], "limit": 1, "total": 3}),
        ) as get:
            result = self.conn.connect(MASTER_KEY, base_url=BASE_URL)
        self.assertEqual(result, f"Connected to Meilisearch at {BASE_URL} (3 indexes).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/indexes")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {MASTER_KEY}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("meilisearch"),
            {"master_key": MASTER_KEY, "base_url": BASE_URL},
        )

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"results": [], "total": 1}),
        ):
            result = self.conn.connect(token=MASTER_KEY, base_url=BASE_URL)
        self.assertTrue(result.startswith(f"Connected to Meilisearch at {BASE_URL}"))

    def test_connect_base_url_trailing_slash_stripped(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"results": [], "total": 0}),
        ) as get:
            result = self.conn.connect(MASTER_KEY, base_url=f"{BASE_URL}/")
        self.assertTrue(result.startswith("Connected to Meilisearch"))
        self.assertEqual(get.call_args.args[0], f"{BASE_URL}/indexes")
        self.assertEqual(store.load("meilisearch")["base_url"], BASE_URL)

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", base_url=BASE_URL)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("meilisearch"))
        get.assert_not_called()

    def test_connect_empty_base_url_errors(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(MASTER_KEY, base_url="")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("base URL", result)
        self.assertIsNone(store.load("meilisearch"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch(
            "zeline.connectors.store.save", wraps=store.save
        ) as save_spy, mock.patch(
            "requests.get", return_value=FakeResponse({"message": "wrong key"}, status=401)
        ):
            result = self.conn.connect("bogus-key", base_url=BASE_URL)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        save_spy.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(MASTER_KEY, base_url=BASE_URL)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn(BASE_URL, result)
        self.assertIsNone(store.load("meilisearch"))

    def test_connect_200_non_json_does_not_crash(self):
        from zeline.connectors import store

        resp = FakeResponse("ok-ish", status=200)
        resp.json = mock.Mock(side_effect=ValueError("nope"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(MASTER_KEY, base_url=BASE_URL)
        self.assertTrue(result.startswith("Connected to Meilisearch"))
        self.assertIsNotNone(store.load("meilisearch"))

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status["connected"], True)
        self.assertIn(BASE_URL, status["detail"])
        self.assertNotIn(MASTER_KEY, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_partial_credentials_not_connected(self):
        from zeline.connectors import store

        store.save("meilisearch", {"master_key": MASTER_KEY})
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Meilisearch disconnected.")
        self.assertEqual(self.conn.disconnect(), "Meilisearch was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "meilisearch")
        self.assertEqual(self.conn.name, "Meilisearch")
        self.assertEqual(self.conn.auth_kind, "pat")


class MeilisearchOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ms-test-"))
        _patch_store(self, self.tmp)
        self.conn = MeilisearchConnector()
        _seed_connected()

    def test_list_indexes(self):
        mapping = {
            ("GET", f"{BASE_URL}/indexes"): (
                {
                    "results": [
                        {"uid": "movies", "primaryKey": "id"},
                        {"uid": "books", "primaryKey": None},
                    ],
                    "limit": 2,
                    "total": 2,
                },
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_indexes(limit=2)
        self.assertEqual(result, "movies (primary key: id)\nbooks (primary key: -)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE_URL}/indexes")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {MASTER_KEY}")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_indexes_limit_clamped(self):
        mapping = {
            ("GET", f"{BASE_URL}/indexes"): (
                {"results": [{"uid": f"i{i}", "primaryKey": "id"} for i in range(100)], "total": 100},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_indexes(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_indexes_min_limit(self):
        mapping = {("GET", f"{BASE_URL}/indexes"): ({"results": [], "total": 0}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.assertEqual(self.conn.list_indexes(limit=0), "No indexes found.")
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 1})

    def test_list_indexes_http_error(self):
        mapping = {("GET", f"{BASE_URL}/indexes"): ({}, 401)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_indexes()
        self.assertEqual(str(ctx.exception), "ERROR: Meilisearch API 401 on /indexes.")

    def test_list_indexes_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_indexes()
        self.assertIn("ERROR: Meilisearch API request failed", str(ctx.exception))

    def test_search_index(self):
        mapping = {
            ("POST", f"{BASE_URL}/indexes/movies/search"): (
                {
                    "hits": [
                        {"id": "1", "title": "Dune"},
                        {"id": "2", "title": "Interstellar"},
                    ],
                    "query": "space",
                },
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.search_index("movies", "space", limit=5)
        self.assertEqual(result, "1: Dune\n2: Interstellar")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE_URL}/indexes/movies/search")
        self.assertEqual(kwargs["json"], {"q": "space", "limit": 5})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {MASTER_KEY}")
        self.assertEqual(kwargs["timeout"], 30)

    def test_search_index_no_results(self):
        mapping = {("POST", f"{BASE_URL}/indexes/movies/search"): ({"hits": []}, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.search_index("movies", "zzz-no-match")
        self.assertEqual(result, "No results for 'zzz-no-match' in index movies.")

    def test_search_index_http_error(self):
        mapping = {("POST", f"{BASE_URL}/indexes/nope/search"): ({"message": "not found"}, 404)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_index("nope", "x")
        self.assertIn("ERROR: Meilisearch API 404", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("meilisearch")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_indexes()
        self.assertIn("zeline connect meilisearch", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search_index("movies", "q")

    def test_secret_not_in_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_index("movies", "q")
        self.assertNotIn(MASTER_KEY, str(ctx.exception))


class MeilisearchRegistryTests(unittest.TestCase):
    def test_meilisearch_registered(self):
        from zeline.connectors import get

        conn = get("meilisearch")
        self.assertIsInstance(conn, MeilisearchConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(meilisearch_mod.MeilisearchConnector.id, "meilisearch")


if __name__ == "__main__":
    unittest.main()
