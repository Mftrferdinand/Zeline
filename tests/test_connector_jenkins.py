"""Tests for the Jenkins connector (username + API token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import jenkins as jenkins_mod
from zeline.connectors.jenkins import JenkinsConnector


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


def _seed_connected():
    from zeline.connectors import store

    store.save(
        "jenkins",
        {
            "username": "aester",
            "api_token": "SECRET-TOKEN",
            "base_url": "https://jenkins.example.com",
            "full_name": "Aester",
        },
    )


class JenkinsConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jenkins-test-"))
        _patch_store(self, self.tmp)
        self.conn = JenkinsConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"fullName": "Aester"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(
                username="aester", api_token="TOKEN", base_url="https://jenkins.example.com/"
            )
        self.assertEqual(result, "Connected to Jenkins as Aester.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://jenkins.example.com/me/api/json")
        self.assertEqual(kwargs["auth"], ("aester", "TOKEN"))
        self.assertEqual(kwargs["timeout"], 30)
        saved = store.load("jenkins")
        self.assertEqual(saved["username"], "aester")
        self.assertEqual(saved["api_token"], "TOKEN")
        self.assertEqual(saved["base_url"], "https://jenkins.example.com")
        self.assertEqual(saved["full_name"], "Aester")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "Invalid token"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(username="aester", api_token="BAD", base_url="https://jenkins.example.com")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("jenkins"))

    def test_connect_missing_fields_stores_nothing(self):
        from zeline.connectors import store

        base = {"username": "aester", "api_token": "TOKEN", "base_url": "https://jenkins.example.com"}
        with mock.patch("requests.get") as get:
            self.assertTrue(self.conn.connect().startswith("ERROR:"))
            self.assertTrue(self.conn.connect(username="aester").startswith("ERROR:"))
            for missing in ("username", "api_token", "base_url"):
                kw = {k: v for k, v in base.items() if k != missing}
                self.assertTrue(self.conn.connect(**kw).startswith("ERROR:"))
        get.assert_not_called()
        self.assertIsNone(store.load("jenkins"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(username="aester", api_token="TOKEN", base_url="https://jenkins.example.com")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "https://jenkins.example.com")
        self.assertNotIn("SECRET-TOKEN", status["detail"])

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Jenkins disconnected.")
        self.assertEqual(self.conn.disconnect(), "Jenkins was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "jenkins")
        self.assertEqual(self.conn.name, "Jenkins")
        self.assertEqual(self.conn.auth_kind, "pat")


class JenkinsOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-jenkins-test-"))
        _patch_store(self, self.tmp)
        self.conn = JenkinsConnector()
        _seed_connected()

    def test_list_jobs(self):
        payload = {"jobs": [{"name": "deploy", "color": "blue"}, {"name": "build", "color": "red"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.list_jobs()
        self.assertEqual(result, "deploy [blue]\nbuild [red]")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://jenkins.example.com/api/json")
        self.assertEqual(kwargs["params"], {"tree": "jobs[name,color]"})
        self.assertEqual(kwargs["auth"], ("aester", "SECRET-TOKEN"))
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_jobs_none(self):
        with mock.patch("requests.request", return_value=FakeResponse({"jobs": []})):
            self.assertEqual(self.conn.list_jobs(), "No jobs found.")

    def test_job_status_success(self):
        payload = {"number": 42, "result": "SUCCESS", "displayName": "#42"}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            result = self.conn.job_status("deploy")
        self.assertEqual(result, "#42 SUCCESS (#42)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://jenkins.example.com/job/deploy/lastBuild/api/json")
        self.assertEqual(kwargs["auth"], ("aester", "SECRET-TOKEN"))

    def test_job_status_building(self):
        payload = {"number": 43, "result": None, "displayName": "#43"}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            self.assertEqual(self.conn.job_status("deploy"), "#43 BUILDING (#43)")

    def test_job_status_no_builds(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.job_status("deploy")
        self.assertEqual(str(ctx.exception), "ERROR: no builds found.")

    def test_api_http_error(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_jobs()
        self.assertIn("ERROR: Jenkins API 403", str(ctx.exception))

    def test_api_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_jobs()
        self.assertIn("ERROR: Jenkins API request failed", str(ctx.exception))

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("jenkins")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_jobs()
        self.assertIn("zeline connect jenkins", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.job_status("deploy")

    def test_secret_never_leaks_in_output(self):
        payload = {"jobs": [{"name": "deploy", "color": "blue"}]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)):
            out = self.conn.list_jobs()
        self.assertNotIn("SECRET-TOKEN", out)
        build = {"number": 1, "result": "SUCCESS", "displayName": "#1"}
        with mock.patch("requests.request", return_value=FakeResponse(build)):
            out = self.conn.job_status("deploy")
        self.assertNotIn("SECRET-TOKEN", out)
        status = self.conn.status()
        self.assertNotIn("SECRET-TOKEN", str(status))


class JenkinsRegistryTests(unittest.TestCase):
    def test_jenkins_registered(self):
        from zeline.connectors import get

        conn = get("jenkins")
        self.assertIsInstance(conn, JenkinsConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(jenkins_mod.JenkinsConnector.id, "jenkins")


if __name__ == "__main__":
    unittest.main()
