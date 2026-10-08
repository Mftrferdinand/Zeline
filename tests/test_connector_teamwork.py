"""Tests for the Teamwork connector (API token + subdomain). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import teamwork as teamwork_mod
from zeline.connectors.teamwork import TeamworkConnector


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

    store.save("teamwork", {"api_token": "SECRET-TOKEN", "subdomain": "acme"})


class TeamworkConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-teamwork-test-"))
        _patch_store(self, self.tmp)
        self.conn = TeamworkConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"person": {"first-name": "Ops"}})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_token="PAT", subdomain="acme")
        self.assertEqual(result, "Connected to Teamwork (acme).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://acme.teamwork.com/projects/api/v3/me.json")
        self.assertEqual(kwargs["auth"], ("PAT", ""))
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("teamwork")
        self.assertEqual(saved["api_token"], "PAT")
        self.assertEqual(saved["subdomain"], "acme")

    def test_connect_subdomain_normalized(self):
        from zeline.connectors import store

        fake = FakeResponse({"person": {}})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_token="PAT", subdomain="  acme/ ")
        self.assertEqual(result, "Connected to Teamwork (acme).")
        args, _ = get.call_args
        self.assertEqual(args[0], "https://acme.teamwork.com/projects/api/v3/me.json")
        self.assertEqual(store.load("teamwork")["subdomain"], "acme")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_token="BAD", subdomain="acme")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("teamwork"))

    def test_connect_missing_credentials_store_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertEqual(
                self.conn.connect(api_token="", subdomain="acme"),
                "ERROR: api_token and subdomain are required.",
            )
            self.assertEqual(
                self.conn.connect(api_token="PAT", subdomain=""),
                "ERROR: api_token and subdomain are required.",
            )
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("teamwork"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_token="PAT", subdomain="acme")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "acme")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Teamwork disconnected.")
        self.assertEqual(self.conn.disconnect(), "Teamwork was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "teamwork")
        self.assertEqual(self.conn.name, "Teamwork")
        self.assertEqual(self.conn.description, "List projects and tasks in Teamwork.")
        self.assertEqual(self.conn.auth_kind, "pat")


class TeamworkOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-teamwork-test-"))
        _patch_store(self, self.tmp)
        self.conn = TeamworkConnector()
        _seed_connected()

    def test_base_uses_stored_subdomain(self):
        self.assertEqual(self.conn._base(), "https://acme.teamwork.com")

    def test_list_projects(self):
        payload = {
            "projects": [
                {"id": "P1", "name": "Website", "status": "active"},
                {"id": "P2", "name": "Mobile", "status": "on-hold"},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_projects(limit=2)
        self.assertEqual(result, "Website [active]\nMobile [on-hold]")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://acme.teamwork.com/projects/api/v3/projects.json")
        self.assertEqual(kwargs["auth"], ("SECRET-TOKEN", ""))
        self.assertEqual(kwargs["params"]["pageSize"], 2)
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_projects_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"projects": []})):
            self.assertEqual(self.conn.list_projects(), "No projects found.")

    def test_list_tasks(self):
        payload = {
            "tasks": [
                {"id": "T1", "name": "Draft copy", "project": {"name": "Website"}},
                {"id": "T2", "name": "Review PR"},
            ]
        }
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_tasks(limit=2)
        self.assertEqual(result, "Draft copy (project: Website)\nReview PR (project: -)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://acme.teamwork.com/projects/api/v3/tasks.json")
        self.assertEqual(kwargs["auth"], ("SECRET-TOKEN", ""))
        self.assertEqual(kwargs["params"]["pageSize"], 2)

    def test_list_tasks_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"tasks": []})):
            self.assertEqual(self.conn.list_tasks(), "No tasks found.")

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"projects": []})) as req:
            self.conn.list_projects(limit=500)
        self.assertEqual(req.call_args[1]["params"]["pageSize"], 100)
        with mock.patch("requests.request", return_value=FakeResponse({"tasks": []})) as req:
            self.conn.list_tasks(limit=0)
        self.assertEqual(req.call_args[1]["params"]["pageSize"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_projects()
        self.assertIn(
            "ERROR: Teamwork API 403 on /projects/api/v3/projects.json.",
            str(ctx.exception),
        )

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_tasks()
        self.assertIn("ERROR: Teamwork API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("teamwork")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_projects()
        self.assertIn("zeline connect teamwork", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_tasks()

    def test_secret_never_leaks_in_output(self):
        payload = {"projects": [{"name": "Website", "status": "active"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_projects()
        self.assertNotIn("SECRET-TOKEN", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-TOKEN", str(status))


class TeamworkRegistryTests(unittest.TestCase):
    def test_teamwork_registered(self):
        from zeline.connectors import get

        conn = get("teamwork")
        self.assertIsInstance(conn, TeamworkConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(teamwork_mod.TeamworkConnector.id, "teamwork")


if __name__ == "__main__":
    unittest.main()
