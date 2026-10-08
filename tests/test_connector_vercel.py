"""Tests for the Vercel connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.vercel import VercelConnector


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

    store.save("vercel", {"token": "SECRET-TOKEN", "user": "acme"})


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-vercel-test-"))
        _patch_store(self, self.tmp)
        self.conn = VercelConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"user": {"username": "acme"}})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="SECRET-TOKEN")
        self.assertEqual(result, "Connected to Vercel as @acme.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.vercel.com/v2/user")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")
        saved = store.load("vercel")
        self.assertEqual(saved["token"], "SECRET-TOKEN")
        self.assertEqual(saved["user"], "acme")

    def test_connect_bad_token_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": {"code": "forbidden"}}, status=403)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("vercel"))

    def test_connect_missing_token(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="  ").startswith("ERROR:"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="T")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@acme")
        self.assertNotIn("SECRET-TOKEN", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Vercel disconnected.")
        self.assertEqual(self.conn.disconnect(), "Vercel was not connected.")


class ListDeploymentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-vercel-test-"))
        _patch_store(self, self.tmp)
        self.conn = VercelConnector()
        _seed_connected(self.tmp)

    def _deployments(self, n=2):
        return {
            "deployments": [
                {"url": f"app-{i}.vercel.app", "state": "READY", "createdAt": 1720000000000 + i}
                for i in range(1, n + 1)
            ]
        }

    def test_list_deployments_formats(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._deployments())) as req:
            result = self.conn.list_deployments()
        self.assertEqual(
            result,
            "app-1.vercel.app [READY] (1720000000001)\napp-2.vercel.app [READY] (1720000000002)",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://api.vercel.com/v6/deployments")
        self.assertEqual(kwargs["params"]["limit"], 10)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer SECRET-TOKEN")

    def test_list_deployments_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse(self._deployments())) as req:
            self.conn.list_deployments(limit=500)
        self.assertEqual(req.call_args[1]["params"]["limit"], 100)
        with mock.patch("requests.request", return_value=FakeResponse(self._deployments())) as req:
            self.conn.list_deployments(limit=0)
        self.assertEqual(req.call_args[1]["params"]["limit"], 1)

    def test_list_deployments_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"deployments": []})):
            self.assertEqual(self.conn.list_deployments(), "No deployments found.")

    def test_list_deployments_api_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_deployments()
        self.assertIn("ERROR: Vercel API 401", str(ctx.exception))

    def test_list_deployments_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_deployments()
        self.assertTrue(str(ctx.exception).startswith("ERROR: Vercel API request failed"))

    def test_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("vercel")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_deployments()
        self.assertIn("zeline connect vercel", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
