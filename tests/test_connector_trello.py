"""Tests for the Trello connector (API key + token via query params). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.trello import TrelloConnector


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

    store.save("trello", {"api_key": "K", "token": "T", "username": "aesdev"})


class TrelloConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-trello-test-"))
        _patch_store(self, self.tmp)
        self.conn = TrelloConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"username": "aesdev", "id": "u1"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(api_key="K", token="T")
        self.assertEqual(result, "Connected to Trello as @aesdev.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.trello.com/1/members/me")
        self.assertEqual(kwargs["params"], {"key": "K", "token": "T"})
        saved = store.load("trello")
        self.assertEqual(saved["api_key"], "K")
        self.assertEqual(saved["token"], "T")
        self.assertEqual(saved["username"], "aesdev")

    def test_connect_missing_params(self):
        from zeline.connectors import store

        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(api_key="K").startswith("ERROR:"))
        self.assertTrue(self.conn.connect(token="T").startswith("ERROR:"))
        self.assertIsNone(store.load("trello"))

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"message": "invalid token"}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(api_key="K", token="BAD")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("trello"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="K", token="T")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_status_masked(self):
        _seed_connected()
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["detail"], "@aesdev")
        blob = repr(status)
        self.assertNotIn("SECRET-API-KEY", blob)
        self.assertNotIn("SECRET-TOKEN", blob)

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_secret_never_in_detail(self):
        _seed_connected()
        from zeline.connectors import store

        store.save("trello", {"api_key": "SUPERSECRETKEY", "token": "SUPERSECRETTOKEN", "username": "x"})
        detail = self.conn.status()["detail"]
        self.assertNotIn("SUPERSECRETKEY", detail)
        self.assertNotIn("SUPERSECRETTOKEN", detail)

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Trello disconnected.")
        self.assertEqual(self.conn.disconnect(), "Trello was not connected.")


class TrelloBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-trello-test-"))
        _patch_store(self, self.tmp)
        self.conn = TrelloConnector()
        _seed_connected()

    def _boards_payload(self):
        return [
            {"id": "b1", "name": "Roadmap", "url": "https://trello.com/b/abc"},
            {"id": "b2", "name": "Ideas", "url": ""},
        ]

    def test_list_boards(self):
        fake = FakeResponse(self._boards_payload())
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_boards()
        self.assertIn("Roadmap — https://trello.com/b/abc (id b1)", result)
        self.assertIn("Ideas (id b2)", result)
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/members/me/boards"))
        self.assertEqual(kwargs["params"]["key"], "K")
        self.assertEqual(kwargs["params"]["token"], "T")

    def test_list_boards_limit_clamped(self):
        fake = FakeResponse([{"id": f"b{i}", "name": f"B{i}"} for i in range(5)])
        with mock.patch("requests.request", return_value=fake):
            result = self.conn.list_boards(limit=2)
        self.assertEqual(len(result.splitlines()), 2)
        # limit=0 clamps to 1 instead of returning everything
        with mock.patch("requests.request", return_value=fake):
            result = self.conn.list_boards(limit=0)
        self.assertEqual(len(result.splitlines()), 1)

    def test_list_boards_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_boards(), "No boards found.")

    def test_list_boards_api_error(self):
        fake = FakeResponse({"message": "nope"}, status=400)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_boards()
        self.assertIn("ERROR: Trello API 400", str(ctx.exception))

    def test_list_boards_when_disconnected(self):
        from zeline.connectors import store

        store.delete("trello")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_boards()
        self.assertIn("zeline connect trello", str(ctx.exception))

    def test_list_boards_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_boards()
        self.assertTrue(str(ctx.exception).startswith("ERROR:"))

    def test_list_cards(self):
        fake = FakeResponse(
            [
                {"id": "c1", "name": "Ship it", "shortLink": "AbCd"},
                {"id": "c2", "name": "WIP"},
            ]
        )
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.list_cards("b1")
        self.assertIn("Ship it (https://trello.com/c/AbCd) (id c1)", result)
        self.assertIn("WIP (id c2)", result)
        args, kwargs = req.call_args
        self.assertTrue(args[1].endswith("/boards/b1/cards"))

    def test_list_cards_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse([])):
            self.assertEqual(self.conn.list_cards("b1"), "No cards on board b1.")

    def test_list_cards_missing_board_id(self):
        self.assertTrue(self.conn.list_cards("").startswith("ERROR:"))
        self.assertTrue(self.conn.list_cards("   ").startswith("ERROR:"))

    def test_list_cards_api_error(self):
        fake = FakeResponse({"message": "not found"}, status=404)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_cards("nope")
        self.assertIn("ERROR: Trello API 404", str(ctx.exception))


class TrelloCreateCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-trello-test-"))
        _patch_store(self, self.tmp)
        self.conn = TrelloConnector()
        _seed_connected()

    def test_create_card(self):
        fake = FakeResponse({"id": "c9", "name": "Fix bug", "shortUrl": "https://trello.com/c/XyZ1"})
        with mock.patch("requests.request", return_value=fake) as req:
            result = self.conn.create_card("l1", "Fix bug", desc="details here")
        self.assertEqual(result, "Card created: Fix bug (https://trello.com/c/XyZ1)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/cards"))
        params = kwargs["params"]
        self.assertEqual(params["idList"], "l1")
        self.assertEqual(params["name"], "Fix bug")
        self.assertEqual(params["desc"], "details here")
        self.assertEqual(params["key"], "K")
        self.assertEqual(params["token"], "T")

    def test_create_card_no_desc(self):
        fake = FakeResponse({"id": "c9", "name": "Fix bug", "shortUrl": "https://trello.com/c/XyZ1"})
        with mock.patch("requests.request", return_value=fake) as req:
            self.conn.create_card("l1", "Fix bug")
        self.assertEqual(req.call_args[1]["params"]["desc"], "")

    def test_create_card_missing_args(self):
        self.assertTrue(self.conn.create_card("", "name").startswith("ERROR:"))
        self.assertTrue(self.conn.create_card("l1", "").startswith("ERROR:"))
        self.assertTrue(self.conn.create_card("l1", "   ").startswith("ERROR:"))

    def test_create_card_api_error(self):
        fake = FakeResponse({"message": "bad list"}, status=400)
        with mock.patch("requests.request", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.create_card("bad", "name")
        self.assertIn("ERROR: Trello API 400", str(ctx.exception))

    def test_create_card_result_has_no_secret(self):
        fake = FakeResponse({"id": "c9", "name": "Fix bug", "shortUrl": "https://trello.com/c/XyZ1"})
        with mock.patch("requests.request", return_value=fake):
            result = self.conn.create_card("l1", "Fix bug")
        self.assertNotIn("SUPERSECRET", result)


class RegistryTests(unittest.TestCase):
    def test_trello_registered(self):
        from zeline.connectors import get

        conn = get("trello")
        self.assertIsInstance(conn, TrelloConnector)
        self.assertEqual(conn.id, "trello")
        self.assertEqual(conn.name, "Trello")
        self.assertEqual(conn.auth_kind, "pat")


if __name__ == "__main__":
    unittest.main()
