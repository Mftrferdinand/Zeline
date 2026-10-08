"""Tests for the Railway connector (personal access token, GraphQL). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors import railway as railway_mod
from zeline.connectors.railway import RailwayConnector

API_URL = "https://backboard.railway.app/graphql/v2"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class NonJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("no JSON here")


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("railway", {"api_token": "seed-token"})


class RailwayConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-rw-test-"))
        _patch_store(self, self.tmp)
        self.conn = RailwayConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"data": {"projects": {"edges": []}}}),
        ) as post:
            result = self.conn.connect(api_token="tok-1")
        self.assertEqual(result, "Connected to Railway.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok-1")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertIn("projects(first: 1)", kwargs["json"]["query"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("railway"), {"api_token": "tok-1"})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"data": {"projects": {"edges": []}}}),
        ) as post:
            result = self.conn.connect(token="tok-alias")
        self.assertEqual(result, "Connected to Railway.")
        self.assertEqual(post.call_args[1]["headers"]["Authorization"], "Bearer tok-alias")

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post") as post:
            result = self.conn.connect()
        self.assertTrue(result.startswith("ERROR:"))
        post.assert_not_called()
        self.assertIsNone(store.load("railway"))

    def test_connect_graphql_errors_no_save(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"data": None, "errors": [{"message": "Unauthorized"}]}),
        ) as post:
            result = self.conn.connect(api_token="bad")
        self.assertTrue(result.startswith("ERROR:"))
        post.assert_called_once()
        self.assertIsNone(store.load("railway"))

    def test_connect_http_401(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect(api_token="bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("railway"))

    def test_connect_request_exception(self):
        with mock.patch("requests.post", side_effect=requests.RequestException("boom")):
            result = self.conn.connect(api_token="tok")
        self.assertTrue(result.startswith("ERROR:"))

    def test_connect_non_json_body(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=NonJsonResponse("nope")):
            result = self.conn.connect(api_token="tok")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("railway"))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Railway disconnected.")
        self.assertEqual(self.conn.disconnect(), "Railway was not connected.")

    def test_status_connected_no_secret(self):
        _seed_connected()
        st = self.conn.status()
        self.assertTrue(st["connected"])
        self.assertNotIn("seed-token", str(st))
        self.assertNotIn("api_token", str(st))

    def test_status_not_connected(self):
        st = self.conn.status()
        self.assertFalse(st["connected"])
        self.assertEqual(st["detail"], "not linked")


class RailwayListProjectsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-rw-test-"))
        _patch_store(self, self.tmp)
        self.conn = RailwayConnector()
        _seed_connected()

    def _ok(self, payload):
        return mock.patch("requests.post", return_value=FakeResponse(payload))

    def test_list_projects_success(self):
        body = {
            "data": {
                "projects": {
                    "edges": [
                        {"node": {"id": "p1", "name": "alpha"}},
                        {"node": {"id": "p2", "name": "beta"}},
                    ]
                }
            }
        }
        with self._ok(body) as post:
            result = self.conn.list_projects(limit=10)
        self.assertIn("p1: alpha", result)
        self.assertIn("p2: beta", result)
        args, kwargs = post.call_args
        self.assertEqual(args[0], API_URL)
        self.assertIn("projects(first: 10)", kwargs["json"]["query"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer seed-token")

    def test_list_projects_clamps_limit(self):
        body = {"data": {"projects": {"edges": []}}}
        with self._ok(body) as post:
            self.conn.list_projects(limit=500)
        self.assertIn("projects(first: 100)", post.call_args[1]["json"]["query"])

    def test_list_projects_empty(self):
        with self._ok({"data": {"projects": {"edges": []}}}):
            result = self.conn.list_projects()
        self.assertEqual(result, "No projects found.")

    def test_list_projects_graphql_errors_raise(self):
        with self._ok({"data": None, "errors": [{"message": "nope"}]}):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_projects()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_list_projects_http_error_raise(self):
        with mock.patch("requests.post", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_projects()
        self.assertIn("500", str(ctx.exception))

    def test_list_projects_not_connected_raise(self):
        from zeline.connectors import store

        store.delete("railway")
        with mock.patch("requests.post") as post:
            with self.assertRaises(RuntimeError):
                self.conn.list_projects()
        post.assert_not_called()


class RailwayModuleTests(unittest.TestCase):
    def test_registered(self):
        from zeline.connectors import get

        conn = get("railway")
        self.assertIsInstance(conn, RailwayConnector)
        self.assertEqual(conn.id, "railway")
        self.assertEqual(conn.name, "Railway")
        self.assertEqual(conn.auth_kind, "pat")


if __name__ == "__main__":
    unittest.main()
