"""Tests for the Fly.io connector (GraphQL personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.flyio import FlyioConnector

API_URL = "https://api.fly.io/graphql"
TOKEN = "fly_fake_token_123"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("flyio", {"api_token": TOKEN})


class FlyioConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-flyio-test-"))
        _patch_store(self, self.tmp)
        self.conn = FlyioConnector()

    def test_connect_success_saves_token(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"data": {"apps": {"nodes": [{"id": "1"}]}}}),
        ) as post:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Fly.io.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("flyio"), {"api_token": TOKEN})

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"data": {"apps": {"nodes": [{"id": "1"}]}}}),
        ):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Fly.io.")

    def test_connect_graphql_errors_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.post",
            return_value=FakeResponse(
                {"errors": [{"message": "Unauthorized"}]}, status=200
            ),
        ):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("flyio"))

    def test_connect_http_error_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("flyio"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch(
            "requests.post", side_effect=requests.ConnectionError("down")
        ):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("flyio"))

    def test_connect_non_json_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post", return_value=FakeResponse(None, status=200)):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("flyio"))

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.post") as post:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("flyio"))
        post.assert_not_called()

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status["connected"], True)
        self.assertNotIn(TOKEN, repr(status))

    def test_status_not_connected(self):
        self.assertEqual(
            self.conn.status(), {"connected": False, "detail": "not linked"}
        )

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Fly.io disconnected.")
        self.assertEqual(self.conn.disconnect(), "Fly.io was not connected.")

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "flyio")
        self.assertEqual(self.conn.name, "Fly.io")
        self.assertEqual(self.conn.description, "Read Fly.io apps via GraphQL.")
        self.assertEqual(self.conn.auth_kind, "pat")


class FlyioOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-flyio-test-"))
        _patch_store(self, self.tmp)
        self.conn = FlyioConnector()
        _seed_connected()

    def _apps_payload(self):
        return {
            "data": {
                "apps": {
                    "nodes": [
                        {"id": "1", "name": "my-app", "status": "running"},
                        {"id": "2", "name": "staging-app", "status": "suspended"},
                    ]
                }
            }
        }

    def test_list_apps(self):
        with mock.patch(
            "requests.post", return_value=FakeResponse(self._apps_payload())
        ) as post:
            result = self.conn.list_apps()
        self.assertIn("my-app", result)
        self.assertIn("running", result)
        self.assertIn("staging-app", result)
        self.assertIn("suspended", result)
        body = post.call_args.kwargs["json"]
        self.assertIn("apps(first: 10)", body["query"])
        self.assertIn("name", body["query"])
        self.assertIn("status", body["query"])

    def test_list_apps_limit_clamped(self):
        with mock.patch(
            "requests.post", return_value=FakeResponse(self._apps_payload())
        ) as post:
            self.conn.list_apps(500)
        self.assertIn("apps(first: 100)", post.call_args.kwargs["json"]["query"])

    def test_list_apps_graphql_errors_raises(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"errors": [{"message": "boom"}]}, status=200),
        ):
            with self.assertRaisesRegex(RuntimeError, "^ERROR:"):
                self.conn.list_apps()

    def test_list_apps_empty(self):
        with mock.patch(
            "requests.post",
            return_value=FakeResponse({"data": {"apps": {"nodes": []}}}),
        ):
            self.assertEqual(self.conn.list_apps(), "No apps found.")

    def test_list_apps_missing_keys_defensive(self):
        with mock.patch("requests.post", return_value=FakeResponse({"data": {}})):
            self.assertEqual(self.conn.list_apps(), "No apps found.")

    def test_graphql_not_connected_raises(self):
        self.conn.disconnect()
        with self.assertRaisesRegex(RuntimeError, "^ERROR:"):
            self.conn.list_apps()


if __name__ == "__main__":
    unittest.main()
