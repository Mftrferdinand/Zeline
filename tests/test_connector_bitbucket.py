"""Tests for the Bitbucket connector. All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from zeline.connectors.bitbucket import BitbucketConnector


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


def _seed_connected():
    from zeline.connectors import store

    store.save("bitbucket", {"username": "aes", "app_password": "PW", "display_name": "Aes Dev"})


class BitbucketConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bitbucket-test-"))
        _patch_store(self, self.tmp)
        self.conn = BitbucketConnector()

    def test_connect_bad_credentials_error_and_saves_nothing(self):
        fake = FakeResponse({"error": {"message": "Unauthorized"}}, status=401)
        with mock.patch("requests.get", return_value=fake) as get, \
                mock.patch("zeline.connectors.store.save") as save:
            result = self.conn.connect(username="aes", app_password="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        save.assert_not_called()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.bitbucket.org/2.0/user")
        self.assertEqual(kwargs["auth"], ("aes", "BAD"))
        self.assertFalse(self.conn.status()["connected"])

    def test_connect_missing_params(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(username="aes").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(app_password="PW").startswith("ERROR:"))

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"display_name": "Aes Dev"})
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(username="aes", app_password="PW")
        self.assertEqual(result, "Connected to Bitbucket as Aes Dev.")
        saved = store.load("bitbucket")
        self.assertEqual(saved["username"], "aes")
        self.assertEqual(saved["app_password"], "PW")
        self.assertEqual(saved["display_name"], "Aes Dev")

    def test_connect_unreachable(self):
        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            result = self.conn.connect(username="aes", app_password="PW")
        self.assertTrue(result.startswith("ERROR:"))


class BitbucketStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bitbucket-test-"))
        _patch_store(self, self.tmp)
        self.conn = BitbucketConnector()

    def test_status_disconnected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_connected_no_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("PW", repr(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Bitbucket disconnected.")
        self.assertEqual(self.conn.disconnect(), "Bitbucket was not connected.")


class BitbucketOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bitbucket-test-"))
        _patch_store(self, self.tmp)
        self.conn = BitbucketConnector()
        _seed_connected()

    def test_list_repos(self):
        payload = {"values": [
            {"full_name": "acme/web", "updated_on": "2026-10-01T08:00:00.000+00:00"},
            {"full_name": "acme/api", "updated_on": "2026-09-20T08:00:00.000+00:00"},
        ]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            out = self.conn.list_repos(limit=2)
        self.assertEqual(out, "acme/web (2026-10-01)\nacme/api (2026-09-20)")
        args, kwargs = req.call_args
        self.assertEqual(args[1], "https://api.bitbucket.org/2.0/repositories")
        self.assertEqual(kwargs["auth"], ("aes", "PW"))
        self.assertEqual(kwargs["params"], {"role": "member", "pagelen": 2})

    def test_list_repos_limit_clamped(self):
        with mock.patch("requests.request", return_value=FakeResponse({"values": []})) as req:
            self.conn.list_repos(limit=500)
        self.assertEqual(req.call_args[1]["params"]["pagelen"], 100)

    def test_list_repos_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"values": []})):
            out = self.conn.list_repos()
        self.assertEqual(out, "No repositories found.")

    def test_list_prs(self):
        payload = {"values": [
            {"id": 12, "title": "Fix crash", "state": "OPEN"},
            {"id": 11, "title": "Bump deps", "state": "MERGED"},
        ]}
        with mock.patch("requests.request", return_value=FakeResponse(payload)) as req:
            out = self.conn.list_prs("acme", "web", limit=2)
        self.assertEqual(out, "#12 Fix crash [OPEN]\n#11 Bump deps [MERGED]")
        args, kwargs = req.call_args
        self.assertEqual(
            args[1], "https://api.bitbucket.org/2.0/repositories/acme/web/pullrequests"
        )

    def test_list_prs_missing_args(self):
        self.assertTrue(self.conn.list_prs("", "web").startswith("ERROR:"))
        self.assertTrue(self.conn.list_prs("acme", "").startswith("ERROR:"))

    def test_list_prs_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"values": []})):
            out = self.conn.list_prs("acme", "web")
        self.assertEqual(out, "No pull requests in acme/web.")

    def test_api_error_raises(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_repos()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_api_not_connected_raises(self):
        from zeline.connectors import store

        store.delete("bitbucket")
        with self.assertRaises(RuntimeError):
            self.conn.list_repos()


if __name__ == "__main__":
    unittest.main()
