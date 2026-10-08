"""Tests for the Jira connector (email + API token, HTTP Basic auth). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.jira import JiraConnector

BASE = "https://acme.atlassian.net"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save(
        "jira",
        {
            "email": "ops@acme.test",
            "token": "SECRET-TOKEN",
            "base_url": BASE,
            "user": "Ops Bot",
        },
    )


class ConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jira-test-"))
        _patch_store(self, self.tmp)
        self.conn = JiraConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"displayName": "Ops Bot", "emailAddress": "ops@acme.test"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(
                email="ops@acme.test", token="SECRET-TOKEN", base_url=BASE
            )
        self.assertEqual(result, "Connected to Jira as Ops Bot.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/rest/api/3/myself")
        self.assertEqual(kwargs["auth"], ("ops@acme.test", "SECRET-TOKEN"))
        saved = store.load("jira")
        self.assertEqual(saved["email"], "ops@acme.test")
        self.assertEqual(saved["token"], "SECRET-TOKEN")
        self.assertEqual(saved["base_url"], BASE)
        self.assertEqual(saved["user"], "Ops Bot")

    def test_connect_strips_trailing_slash(self):
        from zeline.connectors import store

        fake = FakeResponse({"displayName": "Ops Bot"})
        with mock.patch("requests.get", return_value=fake) as get:
            self.conn.connect(email="e@x.t", token="T", base_url=BASE + "/// ")
        self.assertEqual(get.call_args[0][0], f"{BASE}/rest/api/3/myself")
        self.assertEqual(store.load("jira")["base_url"], BASE)

    def test_connect_missing_params(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(email="e@x.t").startswith("ERROR:"))
        self.assertTrue(
            self.conn.connect(email="e@x.t", token="T").startswith("ERROR:")
        )

    def test_connect_rejects_non_https_base_url(self):
        for bad in ("http://acme.atlassian.net", "acme.atlassian.net", "ftp://x.y"):
            result = self.conn.connect(email="e@x.t", token="T", base_url=bad)
            self.assertTrue(result.startswith("ERROR:"), bad)

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"errorMessages": ["bad auth"]}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(email="e@x.t", token="BAD", base_url=BASE)
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("jira"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(email="e@x.t", token="T", base_url=BASE)
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "linked")
        self.assertNotIn("SECRET-TOKEN", str(status))
        self.assertNotIn("ops@acme.test", str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "Jira disconnected.")
        self.assertEqual(self.conn.disconnect(), "Jira was not connected.")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jira-test-"))
        _patch_store(self, self.tmp)
        self.conn = JiraConnector()
        _seed_connected(self.tmp)

    def _issues(self, n=2):
        return {
            "issues": [
                {
                    "key": f"PROJ-{i}",
                    "fields": {
                        "summary": f"summary {i}",
                        "status": {"name": "To Do"},
                    },
                }
                for i in range(1, n + 1)
            ]
        }

    def test_search_formats_issues(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse(self._issues())
        ) as req:
            result = self.conn.search("project = PROJ")
        self.assertEqual(
            result, "PROJ-1: summary 1 [To Do]\nPROJ-2: summary 2 [To Do]"
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE}/rest/api/3/search/jql")
        self.assertEqual(kwargs["auth"], ("ops@acme.test", "SECRET-TOKEN"))
        self.assertEqual(kwargs["params"]["jql"], "project = PROJ")
        self.assertEqual(kwargs["params"]["maxResults"], 10)

    def test_search_limit_clamped(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse(self._issues())
        ) as req:
            self.conn.search("x", limit=500)
        self.assertEqual(req.call_args[1]["params"]["maxResults"], 100)
        with mock.patch(
            "requests.request", return_value=FakeResponse(self._issues())
        ) as req:
            self.conn.search("x", limit=0)
        self.assertEqual(req.call_args[1]["params"]["maxResults"], 1)

    def test_search_empty_jql(self):
        self.assertTrue(self.conn.search("").startswith("ERROR:"))
        self.assertTrue(self.conn.search("   ").startswith("ERROR:"))

    def test_search_no_issues(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse({"issues": []})
        ):
            self.assertEqual(self.conn.search("x"), "No issues found.")

    def test_search_api_error(self):
        with mock.patch(
            "requests.request", return_value=FakeResponse({"error": "bad jql"}, status=400)
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search("bad jql")
        self.assertIn("ERROR: Jira API 400", str(ctx.exception))

    def test_search_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.search("x")
        self.assertTrue(str(ctx.exception).startswith("ERROR: Jira API request failed"))

    def test_search_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("jira")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.search("x")
        self.assertIn("zeline connect jira", str(ctx.exception))


class CreateIssueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jira-test-"))
        _patch_store(self, self.tmp)
        self.conn = JiraConnector()
        _seed_connected(self.tmp)

    def test_create_issue_with_description(self):
        fake = FakeResponse({"key": "PROJ-7", "id": "10007"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_issue(
                "PROJ", "do the thing", description="line one", issue_type="Task"
            )
        self.assertEqual(result, "Created issue PROJ-7.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE}/rest/api/3/issue")
        fields = kwargs["json"]["fields"]
        self.assertEqual(fields["project"], {"key": "PROJ"})
        self.assertEqual(fields["summary"], "do the thing")
        self.assertEqual(fields["issuetype"], {"name": "Task"})
        adf = fields["description"]
        self.assertEqual(adf["type"], "doc")
        self.assertEqual(adf["version"], 1)
        self.assertEqual(
            adf["content"][0]["content"][0]["text"], "line one"
        )

    def test_create_issue_without_description_omits_field(self):
        fake = FakeResponse({"key": "PROJ-8", "id": "10008"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_issue("PROJ", "do it", issue_type="Bug")
        self.assertEqual(result, "Created issue PROJ-8.")
        fields = req.call_args[1]["json"]["fields"]
        self.assertNotIn("description", fields)
        self.assertEqual(fields["issuetype"], {"name": "Bug"})

    def test_create_issue_missing_args(self):
        self.assertTrue(self.conn.create_issue("", "s").startswith("ERROR:"))
        self.assertTrue(self.conn.create_issue("PROJ", " ").startswith("ERROR:"))

    def test_create_issue_api_error(self):
        with mock.patch(
            "requests.request",
            return_value=FakeResponse({"errorMessages": ["nope"]}, status=400),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_issue("PROJ", "s")
        self.assertIn("ERROR: Jira API 400", str(ctx.exception))

    def test_create_issue_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("jira")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_issue("PROJ", "s")
        self.assertIn("zeline connect jira", str(ctx.exception))


class SecretLeakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jira-test-"))
        _patch_store(self, self.tmp)
        self.conn = JiraConnector()
        _seed_connected(self.tmp)

    def test_no_secret_in_outputs(self):
        status = self.conn.status()
        with mock.patch(
            "requests.request", return_value=FakeResponse({"issues": []})
        ):
            search_out = self.conn.search("x")
        with mock.patch(
            "requests.request", return_value=FakeResponse({"key": "PROJ-1"})
        ):
            create_out = self.conn.create_issue("PROJ", "s")
        with mock.patch(
            "requests.get", return_value=FakeResponse({"displayName": "Ops Bot"})
        ):
            connect_out = self.conn.connect(
                email="ops@acme.test", token="SECRET-TOKEN", base_url=BASE
            )
        for blob in (str(status), search_out, create_out, connect_out):
            self.assertNotIn("SECRET-TOKEN", blob)


if __name__ == "__main__":
    unittest.main()
