"""Tests for the Typesense connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import typesense as typesense_mod
from zeline.connectors.typesense import TypesenseConnector

BASE = "http://127.0.0.1:8108"
TOKEN = "ts_fake_api_key_123"


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

    store.save("typesense", {"api_key": TOKEN, "base_url": BASE})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class TypesenseConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ts-test-"))
        _patch_store(self, self.tmp)
        self.conn = TypesenseConnector()

    def test_connect_success_saves_key_and_base_url(self):
        from zeline.connectors import store

        payload = [{"name": "books", "num_documents": 5}, {"name": "users", "num_documents": 2}]
        with mock.patch("zeline.connectors.store.save", wraps=store.save) as save:
            with mock.patch(
                "requests.get", return_value=FakeResponse(payload)
            ) as get:
                result = self.conn.connect(TOKEN, BASE)
        self.assertEqual(result, f"Connected to Typesense at {BASE} (2 collections).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/collections")
        self.assertEqual(kwargs["headers"]["X-TYPESENSE-API-KEY"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)
        save.assert_called_once_with("typesense", {"api_key": TOKEN, "base_url": BASE})
        self.assertEqual(store.load("typesense"), {"api_key": TOKEN, "base_url": BASE})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse([])
        ):
            result = self.conn.connect(token=TOKEN, base_url=BASE)
        self.assertEqual(result, f"Connected to Typesense at {BASE} (0 collections).")

    def test_connect_base_url_kwarg_with_trailing_slash(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse([])) as get:
            result = self.conn.connect(TOKEN, base_url=f"{BASE}/")
        self.assertEqual(result, f"Connected to Typesense at {BASE} (0 collections).")
        self.assertEqual(get.call_args.args[0], f"{BASE}/collections")
        self.assertEqual(store.load("typesense"), {"api_key": TOKEN, "base_url": BASE})

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", BASE)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("typesense"))
        get.assert_not_called()

    def test_connect_empty_base_url_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(TOKEN, "")
        self.assertTrue(result.startswith("ERROR: no base URL provided"))
        self.assertIsNone(store.load("typesense"))
        get.assert_not_called()

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN, BASE)
        self.assertTrue(result.startswith("ERROR: could not reach"))
        self.assertIsNone(store.load("typesense"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("zeline.connectors.store.save") as save:
            with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
                result = self.conn.connect("bogus-key", BASE)
        self.assertTrue(result.startswith("ERROR: Typesense rejected the API key"))
        self.assertIn("401", result)
        save.assert_not_called()
        self.assertIsNone(store.load("typesense"))

    def test_connect_non_json_200_body_does_not_crash(self):
        from zeline.connectors import store

        resp = FakeResponse("ok", status=200)
        resp.json = mock.Mock(side_effect=ValueError("not json"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN, BASE)
        self.assertEqual(result, f"Connected to Typesense at {BASE} (0 collections).")
        self.assertEqual(store.load("typesense"), {"api_key": TOKEN, "base_url": BASE})

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(),
            {"connected": True, "detail": f"linked to {BASE}"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Typesense disconnected.")
        self.assertEqual(self.conn.disconnect(), "Typesense was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "typesense")
        self.assertEqual(self.conn.name, "Typesense")
        self.assertEqual(self.conn.description, "Search Typesense collections.")
        self.assertEqual(self.conn.auth_kind, "pat")


class TypesenseOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-ts-test-"))
        _patch_store(self, self.tmp)
        self.conn = TypesenseConnector()
        _seed_connected()

    def test_list_collections(self):
        mapping = {
            ("GET", f"{BASE}/collections"): (
                [
                    {"name": "books", "num_documents": 5},
                    {"name": "users", "num_documents": 2},
                ],
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_collections()
        self.assertEqual(result, "books (5 documents)\nusers (2 documents)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/collections")
        self.assertEqual(kwargs["headers"]["X-TYPESENSE-API-KEY"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_collections_empty(self):
        mapping = {("GET", f"{BASE}/collections"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_collections(), "No collections found.")

    def test_list_collections_missing_fields(self):
        mapping = {("GET", f"{BASE}/collections"): ([{"name": "weird"}], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_collections(), "weird (? documents)")

    def test_list_collections_http_error(self):
        mapping = {("GET", f"{BASE}/collections"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_collections()
        self.assertEqual(str(ctx.exception), "ERROR: Typesense API 500 on /collections.")

    def test_list_collections_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_collections()
        self.assertIn("ERROR: Typesense API request failed", str(ctx.exception))

    def test_search_collection(self):
        mapping = {
            ("GET", f"{BASE}/collections/books/documents/search"): (
                {"hits": [
                    {"document": {"id": "1", "title": "Ruby Guide", "author": "Matz"}},
                    {"document": {"id": "2", "title": "Python Guide", "author": "Guido"}},
                ]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.search_collection("books", "guide")
        self.assertEqual(
            result,
            "id=1, title=Ruby Guide, author=Matz\nid=2, title=Python Guide, author=Guido",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/collections/books/documents/search")
        self.assertEqual(kwargs["headers"]["X-TYPESENSE-API-KEY"], TOKEN)
        self.assertEqual(kwargs["params"], {"q": "guide", "query_by": "*"})
        self.assertEqual(kwargs["timeout"], 30)

    def test_search_collection_custom_query_by(self):
        mapping = {
            ("GET", f"{BASE}/collections/books/documents/search"): (
                {"hits": [{"document": {"title": "Ruby Guide"}}]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.search_collection("books", "ruby", query_by="title")
        self.assertEqual(result, "title=Ruby Guide")
        self.assertEqual(req.call_args.kwargs["params"], {"q": "ruby", "query_by": "title"})

    def test_search_collection_no_hits(self):
        mapping = {
            ("GET", f"{BASE}/collections/books/documents/search"): ({"hits": []}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(
                self.conn.search_collection("books", "zzz"),
                "No hits in collection books.",
            )

    def test_search_collection_malformed_hits(self):
        mapping = {
            ("GET", f"{BASE}/collections/books/documents/search"): (
                {"hits": [{"document": None}, "junk", {"document": {"id": "9"}}]},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(
                self.conn.search_collection("books", "x"),
                "(empty document)\nid=9",
            )

    def test_search_collection_http_error(self):
        mapping = {
            ("GET", f"{BASE}/collections/nope/documents/search"): ({}, 500),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search_collection("nope", "x")
        self.assertEqual(
            str(ctx.exception),
            "ERROR: Typesense API 500 on /collections/nope/documents/search.",
        )

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("typesense")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_collections()
        self.assertIn("zeline connect typesense", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.search_collection("books", "x")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        self.assertNotIn(TOKEN, repr(self.conn.status()))
        with mock.patch(
            "requests.request", return_value=FakeResponse({}, status=500)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_collections()
        self.assertNotIn(TOKEN, str(ctx.exception))
        with mock.patch("requests.get", return_value=FakeResponse({}, status=403)):
            result = self.conn.connect("brand-new-key", BASE)
        self.assertNotIn("brand-new-key", result)


class TypesenseRegistryTests(unittest.TestCase):
    def test_typesense_registered(self):
        from zeline.connectors import get

        conn = get("typesense")
        self.assertIsInstance(conn, TypesenseConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(typesense_mod.TypesenseConnector.id, "typesense")


if __name__ == "__main__":
    unittest.main()
