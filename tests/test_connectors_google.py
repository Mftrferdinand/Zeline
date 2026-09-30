"""Tests for the Google connector (Gmail/Calendar/Sheets/Drive). All HTTP is mocked."""
from __future__ import annotations

import base64
import time
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import google as google_mod
from zeline.connectors.google import GoogleConnector

# NOTE: other test modules (test_cli/test_agent) evict every ``zeline.*``
# entry from sys.modules in setUp (fresh_cli). String-based
# mock.patch("zeline.connectors....") targets would then re-import a *fresh*
# copy of the module and patch the wrong object. Always patch the exact
# module objects this connector was built with.
_store = google_mod.store
_oauth = google_mod.oauth


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir.

    Patches pathlib.Path.home (like test_connectors.fresh_home) instead of
    the store module, so the redirect also applies to *freshly re-imported*
    copies of zeline.connectors.store — test_cli/test_agent evict every
    zeline.* entry from sys.modules in setUp, and string-based re-imports
    would otherwise escape a module-attribute patch.
    """
    patcher = mock.patch("pathlib.Path.home", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path, token: dict | None = None):
    _store.save(
        "google",
        {
            "client_id": "CID",
            "client_secret": "CSEC",
            "email": "op@example.com",
            "token": token or {"access_token": "AT", "expires_at": int(time.time()) + 3600},
        },
    )


class GoogleConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._tmp_dir())
        self.tmp.mkdir(parents=True, exist_ok=True)
        _patch_store(self, self.tmp)

    def _tmp_dir(self):
        import tempfile

        return tempfile.mkdtemp(prefix="zeline-google-test-")

    def test_connect_with_code_saves_credential(self):
        token = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
        with mock.patch.object(
            _oauth, "exchange_code", return_value=dict(token)
        ) as exch, mock.patch(
            "requests.get",
            return_value=FakeResponse({"email": "op@example.com"}),
        ) as userinfo:
            result = GoogleConnector().connect(
                client_id="CID", client_secret="CSEC", code="AUTHCODE"
            )
        self.assertIn("op@example.com", result)
        exch.assert_called_once()
        args = exch.call_args[0]
        self.assertEqual(args[3], "AUTHCODE")  # code passed to exchange
        userinfo.assert_called_once()
        saved = _store.load("google")
        self.assertEqual(saved["email"], "op@example.com")
        self.assertEqual(saved["token"]["access_token"], "AT")
        self.assertNotIn("CSEC", result)  # secret never echoed

    def test_connect_without_code_uses_local_callback(self):
        token = {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}
        with mock.patch.object(
            _oauth, "build_auth_url", return_value="https://auth.example/x"
        ) as build, mock.patch.object(
            _oauth, "run_local_callback", return_value="CODE123"
        ) as cb, mock.patch.object(
            _oauth, "exchange_code", return_value=dict(token)
        ) as exch, mock.patch(
            "requests.get", return_value=FakeResponse({"email": "op@example.com"})
        ):
            result = GoogleConnector().connect(client_id="CID", client_secret="CSEC")
        self.assertIn("op@example.com", result)
        build.assert_called_once()
        cb.assert_called_once()
        exch.assert_called_once()
        self.assertEqual(exch.call_args[0][3], "CODE123")
        self.assertIsNotNone(_store.load("google"))

    def test_connect_without_code_timeout_gives_manual_instruction(self):
        with mock.patch.object(
            _oauth, "build_auth_url", return_value="https://auth.example/x"
        ), mock.patch.object(
            _oauth, "run_local_callback", return_value=None
        ), mock.patch(
            "requests.get", return_value=FakeResponse({"email": "op@example.com"})
        ):
            result = GoogleConnector().connect(client_id="CID", client_secret="CSEC")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("--code", result)

    def test_connect_rejected_token_saves_nothing(self):
        with mock.patch.object(
            _oauth, "exchange_code", return_value={"access_token": "BAD"}
        ), mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = GoogleConnector().connect(
                client_id="CID", client_secret="CSEC", code="AUTHCODE"
            )
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(_store.load("google"))

    def test_disconnect_and_status(self):
        conn = GoogleConnector()
        self.assertEqual(conn.status(), {"connected": False, "detail": "not linked"})
        _seed_connected(self.tmp)
        self.assertTrue(conn.status()["connected"])
        self.assertIn("op@example.com", conn.status()["detail"])
        self.assertIn("disconnected", conn.disconnect().lower())
        self.assertFalse(conn.status()["connected"])


class GoogleApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._tmp_dir())
        self.tmp.mkdir(parents=True, exist_ok=True)
        _patch_store(self, self.tmp)
        _seed_connected(self.tmp)
        self.conn = GoogleConnector()

    def _tmp_dir(self):
        import tempfile

        return tempfile.mkdtemp(prefix="zeline-google-test-")

    def test_gmail_search_formats_lines(self):
        list_resp = FakeResponse({"messages": [{"id": "m1"}, {"id": "m2"}]})

        def meta(url, **kwargs):
            mid = url.rsplit("/", 1)[-1]
            return FakeResponse(
                {
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": f"Sub {mid}"},
                            {"name": "From", "value": "a@example.com"},
                            {"name": "Date", "value": "Mon, 01 Jan 2024"},
                        ]
                    }
                }
            )

        with mock.patch("requests.request", side_effect=[list_resp, meta("x/m1"), meta("x/m2")]):
            out = self.conn.gmail_search("from:x", limit=2)
        self.assertIn("m1 | Mon, 01 Jan 2024 | a@example.com | Sub m1", out)
        self.assertIn("m2 |", out)

    def test_gmail_search_empty(self):
        with mock.patch("requests.request", return_value=FakeResponse({"messages": []})):
            self.assertEqual(self.conn.gmail_search("zzz"), "No messages found.")

    def test_gmail_read_decodes_body(self):
        body = base64.urlsafe_b64encode("Halo dunia, ini isi email.".encode()).decode()
        payload = FakeResponse(
            {
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Test"},
                        {"name": "From", "value": "b@example.com"},
                        {"name": "Date", "value": "Tue"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": body},
                }
            }
        )
        with mock.patch("requests.request", return_value=payload):
            out = self.conn.gmail_read("m1")
        self.assertIn("Subject: Test", out)
        self.assertIn("Halo dunia, ini isi email.", out)

    def test_gmail_send_posts_raw_mime(self):
        captured = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResponse({"id": "sent123"})

        with mock.patch("requests.request", side_effect=fake_request):
            out = self.conn.gmail_send("t@example.com", "Hi", "body text")
        self.assertIn("sent123", out)
        raw = captured["json"]["raw"]
        decoded = base64.urlsafe_b64decode(raw).decode()
        self.assertIn("To: t@example.com", decoded)
        self.assertIn("Subject: Hi", decoded)
        self.assertIn("body text", decoded)

    def test_calendar_list_formats_events(self):
        payload = FakeResponse(
            {
                "items": [
                    {"summary": "Standup", "start": {"dateTime": "2026-09-30T09:00:00+07:00"}},
                    {"summary": "Libur", "start": {"date": "2026-10-01"}},
                ]
            }
        )
        with mock.patch("requests.request", return_value=payload):
            out = self.conn.calendar_list(limit=5)
        self.assertIn("2026-09-30T09:00:00+07:00 — Standup", out)
        self.assertIn("2026-10-01 — Libur", out)

    def test_sheets_read_tsv(self):
        payload = FakeResponse(
            {"values": [["a", "b", "c"], ["1", "2", "3"], ["x", "y"]]}
        )
        with mock.patch("requests.request", return_value=payload):
            out = self.conn.sheets_read("SID", "Sheet1!A1:C3")
        self.assertEqual(out, "a\tb\tc\n1\t2\t3\nx\ty")

    def test_sheets_read_empty_range(self):
        with mock.patch("requests.request", return_value=FakeResponse({})):
            self.assertEqual(self.conn.sheets_read("SID", "A1:A1"), "Range is empty.")

    def test_drive_list_formats_files(self):
        payload = FakeResponse(
            {
                "files": [
                    {
                        "name": "doc.pdf",
                        "mimeType": "application/pdf",
                        "modifiedTime": "2026-09-29T10:00:00.000Z",
                    }
                ]
            }
        )
        with mock.patch("requests.request", return_value=payload):
            out = self.conn.drive_list(limit=3)
        self.assertIn("doc.pdf (application/pdf,", out)

    def test_api_error_wrapped(self):
        with mock.patch("requests.request", return_value=FakeResponse({}, status=403)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.drive_list()
        self.assertIn("ERROR: Google API 403", str(ctx.exception))

    def test_session_auto_refresh(self):
        from zeline.connectors import store

        _seed_connected(
            self.tmp,
            {"access_token": "OLD", "refresh_token": "RT", "expires_at": int(time.time()) - 10},
        )
        seen = {}

        def fake_request(method, url, **kwargs):
            seen["auth"] = kwargs.get("headers", {}).get("Authorization")
            return FakeResponse({"files": []})

        new_token = {"access_token": "NEW", "refresh_token": "RT", "expires_in": 3600}
        with mock.patch.object(
            _oauth, "refresh_access_token", return_value=dict(new_token)
        ), mock.patch("requests.request", side_effect=fake_request):
            self.conn.drive_list()
        self.assertEqual(seen["auth"], "Bearer NEW")
        self.assertEqual(_store.load("google")["token"]["access_token"], "NEW")

    def test_session_raises_when_not_connected(self):
        _store.delete("google")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn._session()
        self.assertEqual(str(ctx.exception), "not connected")


class GoogleToolHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._tmp_dir())
        self.tmp.mkdir(parents=True, exist_ok=True)
        _patch_store(self, self.tmp)

    def _tmp_dir(self):
        import tempfile

        return tempfile.mkdtemp(prefix="zeline-google-test-")

    def test_disconnected_tool_message(self):
        from zeline.tools import _connector_tool

        for method, kwargs in [
            ("gmail_search", {"query": "x"}),
            ("gmail_read", {"message_id": "m1"}),
            ("gmail_send", {"to": "a@b.c", "subject": "s", "body": "b"}),
            ("calendar_list", {}),
            ("sheets_read", {"spreadsheet_id": "s", "range_name": "r"}),
            ("drive_list", {}),
        ]:
            out = _connector_tool("google", method, **kwargs)
            self.assertIn("zeline connect google", out, method)

    def test_connected_tool_dispatches(self):
        from zeline.tools import _connector_tool

        _seed_connected(self.tmp)
        with mock.patch("requests.request", return_value=FakeResponse({"files": []})):
            out = _connector_tool("google", "drive_list")
        self.assertEqual(out, "No files found.")


if __name__ == "__main__":
    unittest.main()
