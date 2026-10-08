"""Tests for the dev.to connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import devto as devto_mod
from zeline.connectors.devto import DevToConnector


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

    store.save(
        "devto",
        {
            "api_key": "SECRET-API-KEY",
            "username": "aester",
        },
    )


class DevToConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-devto-test-"))
        _patch_store(self, self.tmp)
        self.conn = DevToConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"username": "aester", "name": "Aes"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="API-KEY")
        self.assertEqual(result, "Connected to dev.to as @aester.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://dev.to/api/articles/me")
        self.assertEqual(kwargs["headers"]["api-key"], "API-KEY")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("devto")
        self.assertEqual(saved["api_key"], "API-KEY")
        self.assertEqual(saved["username"], "aester")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": "unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("devto"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(api_key="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("devto"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="API-KEY")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_connect_unreadable_response(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad json")
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="API-KEY")
        self.assertTrue(result.startswith("ERROR:"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@aester")
        self.assertNotIn("SECRET-API-KEY", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "dev.to disconnected.")
        self.assertEqual(self.conn.disconnect(), "dev.to was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "devto")
        self.assertEqual(self.conn.name, "dev.to")
        self.assertEqual(self.conn.description, "List and publish articles on dev.to.")
        self.assertEqual(self.conn.auth_kind, "pat")


class DevToOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-devto-test-"))
        _patch_store(self, self.tmp)
        self.conn = DevToConnector()
        _seed_connected()

    def test_list_articles(self):
        payload = [
            {"title": "Hello dev.to", "url": "https://dev.to/aester/hello"},
            {"title": "Second post", "url": "https://dev.to/aester/second"},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_articles(limit=5)
        self.assertEqual(
            result,
            "Hello dev.to — https://dev.to/aester/hello\nSecond post — https://dev.to/aester/second",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://dev.to/api/articles")
        self.assertEqual(kwargs["headers"]["api-key"], "SECRET-API-KEY")
        self.assertEqual(kwargs["params"], {"per_page": 5})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_articles_with_tag(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            result = self.conn.list_articles(limit=10, tag="python")
        self.assertEqual(result, "No articles found.")
        self.assertEqual(req.call_args[1]["params"], {"per_page": 10, "tag": "python"})

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_articles(limit=500)
        self.assertEqual(req.call_args[1]["params"]["per_page"], 100)
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_articles(limit=0)
        self.assertEqual(req.call_args[1]["params"]["per_page"], 1)

    def test_create_article(self):
        payload = {"url": "https://dev.to/aester/draft-1", "id": 42}
        with mock.patch("requests.request", return_value=FakeResponse(payload, status=201)) as req:
            result = self.conn.create_article("My title", "# Body", published=True)
        self.assertEqual(result, "Article created: https://dev.to/aester/draft-1")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://dev.to/api/articles")
        self.assertEqual(kwargs["headers"]["api-key"], "SECRET-API-KEY")
        article = kwargs["json"]["article"]
        self.assertEqual(article["title"], "My title")
        self.assertEqual(article["body_markdown"], "# Body")
        self.assertTrue(article["published"])

    def test_create_article_defaults_draft(self):
        payload = {"url": "https://dev.to/aester/draft-2"}
        with mock.patch("requests.request", return_value=FakeResponse(payload, status=201)) as req:
            self.conn.create_article("Draft", "body")
        self.assertFalse(req.call_args[1]["json"]["article"]["published"])

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=422)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_article("T", "B")
        self.assertIn("ERROR: dev.to API 422 on /articles.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_articles()
        self.assertIn("ERROR: dev.to API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("devto")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_articles()
        self.assertIn("zeline connect devto", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.create_article("T", "B")

    def test_secret_never_leaks_in_output(self):
        payload = [{"title": "Post", "url": "https://dev.to/aester/post"}]
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_articles()
        self.assertNotIn("SECRET-API-KEY", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-API-KEY", str(status))


class DevToRegistryTests(unittest.TestCase):
    def test_devto_registered(self):
        from zeline.connectors import get

        conn = get("devto")
        self.assertIsInstance(conn, DevToConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(devto_mod.DevToConnector.id, "devto")


if __name__ == "__main__":
    unittest.main()
