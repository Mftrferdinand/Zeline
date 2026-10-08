"""Tests for the Apollo connector (API key via X-Api-Key header). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import apollo as apollo_mod
from zeline.connectors.apollo import ApolloConnector

TOKEN = "apollo_fake_api_key_123"
PEOPLE = [
    {"name": "Jane Doe", "title": "VP Sales", "organization": {"name": "Acme Inc"}},
    {"name": "John Smith", "title": "CTO", "organization": {"name": "Beta Co"}},
]


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

    store.save("apollo", {"api_key": TOKEN})


class ApolloConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-apollo-test-"))
        _patch_store(self, self.tmp)
        self.conn = ApolloConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=200)) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Apollo.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.apollo.io/v1/auth/health")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("apollo"), {"api_key": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=200)):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Apollo.")

    def test_connect_missing_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR: no API key"))
        self.assertIsNone(store.load("apollo"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)) as get:
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Apollo rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("apollo"))
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.apollo.io/v1/auth/health")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], "bogus-token")
        self.assertEqual(store.load("apollo"), None)

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach Apollo API"))
        self.assertIsNone(store.load("apollo"))

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status["connected"], True)
        self.assertNotIn(TOKEN, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Apollo disconnected.")
        self.assertEqual(self.conn.disconnect(), "Apollo was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "apollo")
        self.assertEqual(self.conn.name, "Apollo")
        self.assertEqual(self.conn.description, "Search B2B people data via Apollo.io.")
        self.assertEqual(self.conn.auth_kind, "pat")


class ApolloOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-apollo-test-"))
        _patch_store(self, self.tmp)
        self.conn = ApolloConnector()
        _seed_connected()

    def test_people_search(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"people": list(PEOPLE)}),
        ) as post:
            result = self.conn.people_search("sales", limit=2)
        self.assertEqual(
            result,
            "Jane Doe — VP Sales (Acme Inc)\nJohn Smith — CTO (Beta Co)",
        )
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.apollo.io/v1/mixed_people/search")
        self.assertEqual(
            kwargs["json"],
            {"q_keywords": "sales", "page": 1, "per_page": 2},
        )
        self.assertEqual(kwargs["headers"]["X-Api-Key"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)

    def test_people_search_default_limit(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"people": list(PEOPLE)}),
        ) as post:
            self.conn.people_search("sales")
        self.assertEqual(post.call_args.kwargs["json"]["per_page"], 10)

    def test_people_search_limit_clamped(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"people": list(PEOPLE)}),
        ) as post:
            self.conn.people_search("sales", limit=500)
        self.assertEqual(post.call_args.kwargs["json"]["per_page"], 100)

    def test_people_search_min_limit(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"people": list(PEOPLE)}),
        ) as post:
            self.conn.people_search("sales", limit=0)
        self.assertEqual(post.call_args.kwargs["json"]["per_page"], 1)

    def test_people_search_missing_fields(self):
        payload = {
            "people": [
                {"name": "Jane Doe"},
                {"title": "CTO"},
                {},
            ]
        }
        with mock.patch("requests.post", return_value=FakeResponse(payload)):
            result = self.conn.people_search("x")
        self.assertEqual(result, "Jane Doe\n(no name) — CTO\n(no name)")

    def test_people_search_empty(self):
        with mock.patch("requests.post", return_value=FakeResponse({"people": []})):
            self.assertEqual(self.conn.people_search("zzz"), "No people found.")

    def test_people_search_401_raises(self):
        with mock.patch("requests.post", return_value=FakeResponse({}, status=401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.people_search("sales")
        self.assertEqual(str(ctx.exception), "ERROR: Apollo API 401 on /mixed_people/search.")

    def test_people_search_network_error(self):
        import requests

        with mock.patch("requests.post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.people_search("sales")
        self.assertIn("ERROR: Apollo API request failed", str(ctx.exception))

    def test_people_search_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.post", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.people_search("sales")
        self.assertIn("unreadable response", str(ctx.exception))

    def test_enrich_person(self):
        payload = {
            "person": {
                "name": "Jane Doe",
                "title": "VP Sales",
                "organization": {"name": "Acme Inc"},
            }
        }
        with mock.patch("requests.post", return_value=FakeResponse(payload)) as post:
            result = self.conn.enrich_person("jane@example.com")
        self.assertEqual(result, "Jane Doe — VP Sales (Acme Inc)")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.apollo.io/v1/people/match")
        self.assertEqual(kwargs["json"], {"email": "jane@example.com"})
        self.assertEqual(kwargs["headers"]["X-Api-Key"], TOKEN)

    def test_enrich_person_minimal(self):
        with mock.patch("requests.post", return_value=FakeResponse({"person": {"name": "X"}})):
            self.assertEqual(self.conn.enrich_person("x@example.com"), "X")

    def test_enrich_person_no_match(self):
        with mock.patch("requests.post", return_value=FakeResponse({})):
            self.assertEqual(self.conn.enrich_person("nobody@example.com"), "(no name)")

    def test_enrich_person_429_raises(self):
        with mock.patch("requests.post", return_value=FakeResponse({}, status=429)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.enrich_person("jane@example.com")
        self.assertIn("ERROR: Apollo API 429", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("apollo")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.people_search("sales")
        self.assertIn("zeline connect apollo", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.enrich_person("jane@example.com")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.post", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.people_search("sales")
        self.assertNotIn(TOKEN, str(ctx.exception))


class ApolloRegistryTests(unittest.TestCase):
    def test_apollo_registered(self):
        from zeline.connectors import get

        conn = get("apollo")
        self.assertIsInstance(conn, ApolloConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(apollo_mod.ApolloConnector.id, "apollo")


if __name__ == "__main__":
    unittest.main()
