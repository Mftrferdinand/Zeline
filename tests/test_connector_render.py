"""Tests for the Render connector (API key). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import render as render_mod
from zeline.connectors.render import RenderConnector


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

    store.save("render", {"api_key": "SECRET-API-KEY"})


class RenderConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-render-test-"))
        _patch_store(self, self.tmp)
        self.conn = RenderConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse([{"service": {"id": "srv-1", "name": "web"}}])
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="KEY123")
        self.assertEqual(result, "Connected to Render.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.render.com/v1/services")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer KEY123")
        self.assertEqual(kwargs["params"], {"limit": 1})
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("render")
        self.assertEqual(saved["api_key"], "KEY123")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("render"))

    def test_connect_empty_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertEqual(self.conn.connect(), "ERROR: api_key is required.")
            self.assertEqual(self.conn.connect(api_key="  "), "ERROR: api_key is required.")
        get.assert_not_called()
        self.assertIsNone(store.load("render"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET-API-KEY", status["detail"])
        self.assertNotIn("SECRET-API-KEY", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Render disconnected.")
        self.assertEqual(self.conn.disconnect(), "Render was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "render")
        self.assertEqual(self.conn.name, "Render")
        self.assertEqual(self.conn.auth_kind, "pat")
        self.assertEqual(self.conn.description, "List Render services and deploys.")


class RenderOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-render-test-"))
        _patch_store(self, self.tmp)
        self.conn = RenderConnector()
        _seed_connected()

    def test_list_services(self):
        payload = [
            {"service": {"id": "srv-1", "name": "web-app", "type": "web_service", "suspended": None}},
            {"service": {"id": "srv-2", "name": "worker", "type": "background_worker", "suspended": "suspended"}},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_services(limit=2)
        self.assertEqual(
            result,
            "web-app [web_service] (active)\nworker [background_worker] (suspended)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.render.com/v1/services")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-API-KEY")
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_services_none(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_services(), "No services found.")

    def test_list_deploys(self):
        payload = [
            {
                "deploy": {
                    "id": "dep-abcdef12",
                    "status": "live",
                    "commit": {"message": "deploy new feature to production"},
                }
            },
            {"deploy": {"id": "dep-9876abcd", "status": "build_failed", "commit": None}},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_deploys("srv-1", limit=2)
        self.assertEqual(
            result,
            "dep-abcd live (deploy new feature to production)\ndep-9876 build_failed ()",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://api.render.com/v1/services/srv-1/deploys")
        self.assertEqual(kwargs["params"], {"limit": 2})

    def test_list_deploys_none(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_deploys("srv-1"), "No deploys found.")

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_services(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_deploys("srv-1", limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_services()
        self.assertIn("ERROR: Render API 403 on /services.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_deploys("srv-1")
        self.assertIn("ERROR: Render API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("render")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_services()
        self.assertIn("zeline connect render", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_deploys("srv-1")

    def test_secret_never_leaks_in_output(self):
        payload = [{"service": {"id": "srv-1", "name": "web", "type": "web_service", "suspended": None}}]
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_services()
        self.assertNotIn("SECRET-API-KEY", out)
        self.assertNotIn("SECRET-API-KEY", str(self.conn.status()))


class RenderRegistryTests(unittest.TestCase):
    def test_render_registered(self):
        from zeline.connectors import get

        conn = get("render")
        self.assertIsInstance(conn, RenderConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(render_mod.RenderConnector.id, "render")


if __name__ == "__main__":
    unittest.main()
