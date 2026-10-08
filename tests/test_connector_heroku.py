"""Tests for the Heroku connector (mocked requests, no network)."""
from __future__ import annotations

import unittest
from unittest import mock

import requests

from zeline.connectors import heroku


def _resp(status=200, payload=None, json_error=False):
    resp = mock.Mock()
    resp.status_code = status
    if json_error:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = payload
    return resp


def _patch_store(connected=True):
    secret = {"api_token": "sekret"} if connected else {}
    load = mock.patch.object(heroku.store, "load", return_value=secret)
    save = mock.patch.object(heroku.store, "save")
    delete = mock.patch.object(heroku.store, "delete", return_value=connected)
    return load, save, delete


class HerokuConnectTest(unittest.TestCase):
    def setUp(self):
        load, save, delete = _patch_store(connected=False)
        self.load = load.start()
        self.save = save.start()
        self.delete = delete.start()
        self._patchers = (load, save, delete)
        self.conn = heroku.HerokuConnector()

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_connect_success_saves(self):
        with mock.patch.object(
            heroku.requests, "get", return_value=_resp(200, {"email": "a@b.c"})
        ) as get:
            result = self.conn.connect(api_token="tok")
        self.assertIn("a@b.c", result)
        self.save.assert_called_once_with("heroku", {"api_token": "tok"})
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(
            kwargs["headers"]["Accept"], "application/vnd.heroku+json; version=3"
        )

    def test_connect_token_alias(self):
        with mock.patch.object(heroku.requests, "get", return_value=_resp(200, {})):
            result = self.conn.connect(token="tok-alias")
        self.assertNotIn("ERROR", result)
        self.save.assert_called_once_with("heroku", {"api_token": "tok-alias"})

    def test_connect_empty_token(self):
        result = self.conn.connect(api_token="   ")
        self.assertTrue(result.startswith("ERROR:"))
        self.save.assert_not_called()

    def test_connect_401_no_save(self):
        with mock.patch.object(heroku.requests, "get", return_value=_resp(401)):
            result = self.conn.connect(api_token="bad")
        self.assertTrue(result.startswith("ERROR:"))
        self.save.assert_not_called()

    def test_connect_request_exception(self):
        with mock.patch.object(
            heroku.requests, "get", side_effect=requests.RequestException("down")
        ):
            result = self.conn.connect(api_token="tok")
        self.assertTrue(result.startswith("ERROR:"))
        self.save.assert_not_called()

    def test_connect_non_json_200_no_crash(self):
        with mock.patch.object(
            heroku.requests, "get", return_value=_resp(200, json_error=True)
        ):
            result = self.conn.connect(api_token="tok")
        self.assertNotIn("ERROR", result)
        self.save.assert_called_once_with("heroku", {"api_token": "tok"})

    def test_disconnect_connected(self):
        load, save, delete = _patch_store(connected=True)
        load.start(); save.start(); delete.start()
        try:
            self.assertEqual(heroku.HerokuConnector().disconnect(), "Heroku disconnected.")
        finally:
            load.stop(); save.stop(); delete.stop()

    def test_disconnect_not_connected(self):
        self.assertEqual(
            self.conn.disconnect(), "Heroku was not connected."
        )


class HerokuStatusTest(unittest.TestCase):
    def test_status_not_connected(self):
        load, save, delete = _patch_store(connected=False)
        load.start(); save.start(); delete.start()
        try:
            st = heroku.HerokuConnector().status()
        finally:
            load.stop(); save.stop(); delete.stop()
        self.assertEqual(st, {"connected": False, "detail": "not linked"})

    def test_status_connected_no_secret(self):
        load, save, delete = _patch_store(connected=True)
        load.start(); save.start(); delete.start()
        try:
            st = heroku.HerokuConnector().status()
        finally:
            load.stop(); save.stop(); delete.stop()
        self.assertTrue(st["connected"])
        self.assertNotIn("sekret", str(st))


class HerokuOpsTest(unittest.TestCase):
    def setUp(self):
        load, save, delete = _patch_store(connected=True)
        self.load = load.start()
        self.save = save.start()
        self.delete = delete.start()
        self._patchers = (load, save, delete)
        self.conn = heroku.HerokuConnector()

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_list_apps(self):
        apps = [
            {"name": "app-one", "region": {"name": "us"}, "stack": {"name": "heroku-24"}},
            {"name": "app-two", "region": {"name": "eu"}, "stack": {"name": "heroku-22"}},
        ]
        with mock.patch.object(heroku.requests, "request", return_value=_resp(200, apps)):
            out = self.conn.list_apps(limit=10)
        self.assertIn("app-one", out)
        self.assertIn("region: us", out)
        self.assertIn("stack: heroku-24", out)
        self.assertIn("app-two", out)

    def test_list_apps_not_connected(self):
        load, _, _ = _patch_store(connected=False)
        load.start()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                heroku.HerokuConnector().list_apps()
        finally:
            load.stop()
        self.assertIn("ERROR", str(ctx.exception))

    def test_list_apps_500_raises(self):
        with mock.patch.object(heroku.requests, "request", return_value=_resp(500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_apps()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_list_apps_request_exception_raises(self):
        with mock.patch.object(
            heroku.requests, "request", side_effect=requests.RequestException("boom")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_apps()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
