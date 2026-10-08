"""Tests for the GitLab connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import gitlab as gitlab_mod
from zeline.connectors.gitlab import GitLabConnector


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
        "gitlab",
        {
            "token": "SECRET-TOKEN",
            "base_url": "https://gitlab.com/api/v4",
            "username": "aester",
        },
    )


class GitLabConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-gitlab-test-"))
        _patch_store(self, self.tmp)
        self.conn = GitLabConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"username": "aester", "id": 123})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="PAT")
        self.assertIn("Connected to GitLab as @aester.", result)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://gitlab.com/api/v4/user")
        self.assertEqual(kwargs["headers"]["PRIVATE-TOKEN"], "PAT")
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("gitlab")
        self.assertEqual(saved["token"], "PAT")
        self.assertEqual(saved["username"], "aester")
        self.assertEqual(saved["base_url"], "https://gitlab.com/api/v4")

    def test_connect_success_custom_base_url(self):
        from zeline.connectors import store

        fake = FakeResponse({"username": "ops"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(token="PAT", base_url="https://git.example.com/")
        self.assertIn("Connected to GitLab as @ops.", result)
        args, _ = get.call_args
        self.assertEqual(args[0], "https://git.example.com/api/v4/user")
        saved = store.load("gitlab")
        self.assertEqual(saved["base_url"], "https://git.example.com/api/v4")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "401 Unauthorized"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("gitlab"))

    def test_connect_empty_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(token="  ").startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("gitlab"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(token="PAT")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@aester")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "GitLab disconnected.")
        self.assertEqual(self.conn.disconnect(), "GitLab was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "gitlab")
        self.assertEqual(self.conn.name, "GitLab")
        self.assertEqual(self.conn.auth_kind, "pat")


class GitLabOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-gitlab-test-"))
        _patch_store(self, self.tmp)
        self.conn = GitLabConnector()
        _seed_connected()

    def test_list_projects(self):
        payload = [
            {"path_with_namespace": "aester/app", "description": "My app"},
            {"path_with_namespace": "aester/empty", "description": ""},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_projects(limit=2)
        self.assertEqual(result, "aester/app — My app\naester/empty")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://gitlab.com/api/v4/projects")
        self.assertEqual(kwargs["headers"]["PRIVATE-TOKEN"], "SECRET-TOKEN")
        params = kwargs["params"]
        self.assertEqual(params["membership"], "true")
        self.assertEqual(params["order_by"], "last_activity_at")
        self.assertEqual(params["per_page"], 2)

    def test_list_projects_none(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_projects(), "No projects found.")

    def test_list_merge_requests(self):
        payload = [
            {
                "iid": 42,
                "title": "Add login",
                "source_branch": "feat/login",
                "target_branch": "main",
            }
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_merge_requests(state="opened", limit=5)
        self.assertEqual(result, "!42 Add login (feat/login→main)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://gitlab.com/api/v4/merge_requests")
        params = kwargs["params"]
        self.assertEqual(params["scope"], "all")
        self.assertEqual(params["state"], "opened")
        self.assertEqual(params["per_page"], 5)

    def test_list_merge_requests_none(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_merge_requests(state="merged"), "No merged merge requests.")

    def test_list_issues(self):
        payload = [
            {"iid": 7, "title": "Fix crash", "labels": ["bug", "urgent"]},
            {"iid": 8, "title": "Docs", "labels": []},
        ]
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_issues(state="opened", limit=10)
        self.assertEqual(result, "#7 Fix crash [bug,urgent]\n#8 Docs")
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://gitlab.com/api/v4/issues")
        self.assertEqual(kwargs["params"]["scope"], "all")
        self.assertEqual(kwargs["params"]["state"], "opened")

    def test_list_issues_none(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_issues(), "No opened issues.")

    def test_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_projects(limit=500)
        self.assertEqual(req.call_args[1]["params"]["per_page"], 100)
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_issues(limit=0)
        self.assertEqual(req.call_args[1]["params"]["per_page"], 1)

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_projects()
        self.assertIn("ERROR: GitLab API 403 on /projects.", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_issues()
        self.assertIn("ERROR: GitLab API request failed", str(ctx.exception))

    def test_operation_uses_custom_base_url(self):
        from zeline.connectors import store

        store.save(
            "gitlab",
            {
                "token": "T",
                "base_url": "https://git.example.com/api/v4",
                "username": "ops",
            },
        )
        with mock.patch("requests.request", return_value=FakeResponse([])) as req:
            self.conn.list_projects()
        self.assertEqual(req.call_args[0][1], "https://git.example.com/api/v4/projects")

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("gitlab")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_projects()
        self.assertIn("zeline connect gitlab", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.list_merge_requests()
        with self.assertRaises(RuntimeError):
            self.conn.list_issues()

    def test_secret_never_leaks_in_output(self):
        payload = [{"path_with_namespace": "aester/app", "description": ""}]
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_projects()
        self.assertNotIn("SECRET-TOKEN", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-TOKEN", str(status))


class GitLabRegistryTests(unittest.TestCase):
    def test_gitlab_registered(self):
        from zeline.connectors import get

        conn = get("gitlab")
        self.assertIsInstance(conn, GitLabConnector)

    def test_module_import_does_not_leak(self):
        # Importing the module only registers; it performs no network I/O.
        self.assertEqual(gitlab_mod.GitLabConnector.id, "gitlab")


if __name__ == "__main__":
    unittest.main()
