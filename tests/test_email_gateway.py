"""Tests for the email gateway (zeline/gateways/email.py). All network mocked."""

import json
import os
import stat
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_gateway():
    from zeline.gateways import email as gw
    return gw


class InfoValidateTests(unittest.TestCase):
    def test_info(self):
        gw = _make_gateway()
        i = gw.info()
        self.assertIn("label", i)
        self.assertIn("hint", i)

    def test_validate_missing(self):
        gw = _make_gateway()
        errs = gw.validate_config({})
        self.assertGreaterEqual(len(errs), 6)  # 6 required keys

    def test_validate_ok(self):
        gw = _make_gateway()
        cfg = {
            "imap_host": "imap.example.com", "imap_user": "u", "imap_pass": "p",
            "smtp_host": "smtp.example.com", "smtp_user": "u", "smtp_pass": "p",
            "allowed_senders": ["owner@example.com"],
        }
        self.assertEqual(gw.validate_config(cfg), [])

    def test_validate_empty_allowed_senders_rejected(self):
        # A2-M1: empty allowlist = accept mail from anyone. Must refuse.
        gw = _make_gateway()
        cfg = {
            "imap_host": "imap.example.com", "imap_user": "u", "imap_pass": "p",
            "smtp_host": "smtp.example.com", "smtp_user": "u", "smtp_pass": "p",
            "allowed_senders": [],
        }
        errs = gw.validate_config(cfg)
        self.assertTrue(any("allowed_senders" in e for e in errs),
                        f"expected allowed_senders error, got: {errs}")

    def test_validate_missing_allowed_senders_rejected(self):
        # A2-M1: missing key is the same as empty.
        gw = _make_gateway()
        cfg = {
            "imap_host": "imap.example.com", "imap_user": "u", "imap_pass": "p",
            "smtp_host": "smtp.example.com", "smtp_user": "u", "smtp_pass": "p",
        }
        errs = gw.validate_config(cfg)
        self.assertTrue(any("allowed_senders" in e for e in errs),
                        f"expected allowed_senders error, got: {errs}")

    def test_start_reconnect_backoff_present(self):
        # A2-L3: exponential backoff code paths exist in start().
        import inspect
        from zeline.gateways import email as gw
        src = inspect.getsource(gw.start)
        self.assertIn("backoff = 10", src)
        self.assertIn("min(backoff * 2, 300)", src)


class ConfigFileTests(unittest.TestCase):
    def test_save_config_0600(self):
        gw = _make_gateway()
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "email.json"
            with patch.object(gw, "_CONFIG_PATH", fake), \
                 patch.object(gw, "_GATEWAY_DIR", Path(tmp)):
                gw.save_config({"imap_pass": "secret"})
                mode = stat.S_IMODE(os.stat(fake).st_mode)
                self.assertEqual(mode, 0o600)
                data = json.loads(fake.read_text())
                self.assertEqual(data["imap_pass"], "secret")


class ParseTests(unittest.TestCase):
    RAW = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: me@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Date: Thu, 08 Oct 2026 10:00:00 +0000\r\n"
        b"Message-ID: <abc123@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hi there, this is the body.\r\n"
    )

    def test_parse(self):
        gw = _make_gateway()
        p = gw._parse_message(self.RAW)
        self.assertEqual(p["from"], "alice@example.com")
        self.assertEqual(p["subject"], "Hello")
        self.assertEqual(p["message_id"], "<abc123@example.com>")
        self.assertIn("Hi there", p["body"])

    def test_parse_multipart_prefers_plain(self):
        gw = _make_gateway()
        raw = (
            b"From: b@example.com\r\nSubject: t\r\nMessage-ID: <x>\r\n"
            b"Content-Type: multipart/alternative; boundary=B\r\n\r\n"
            b"--B\r\nContent-Type: text/plain\r\n\r\nplain body\r\n"
            b"--B\r\nContent-Type: text/html\r\n\r\n<html>html</html>\r\n"
            b"--B--\r\n"
        )
        p = gw._parse_message(raw)
        self.assertIn("plain body", p["body"])
        self.assertNotIn("<html>", p["body"])


class FilterTests(unittest.TestCase):
    def _parsed(self, **kw):
        base = {
            "from": "alice@example.com", "from_header": "Alice <alice@example.com>",
            "subject": "Hi", "date": "", "message_id": "<1>",
            "auto_submitted": "", "precedence": "",
            "x_auto_response_suppress": "", "body": "hi",
        }
        base.update(kw)
        return base

    def test_allowed(self):
        gw = _make_gateway()
        ok, _ = gw._should_process(
            self._parsed(), {"allowed_senders": ["alice@example.com"]}, "me@example.com")
        self.assertTrue(ok)

    def test_not_allowed(self):
        gw = _make_gateway()
        ok, reason = gw._should_process(
            self._parsed(), {"allowed_senders": ["bob@example.com"]}, "me@example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "sender-not-allowed")

    def test_empty_allowed_accepts_all(self):
        gw = _make_gateway()
        ok, _ = gw._should_process(self._parsed(), {}, "me@example.com")
        self.assertTrue(ok)

    def test_own_address(self):
        gw = _make_gateway()
        ok, reason = gw._should_process(
            self._parsed(**{"from": "me@example.com"}), {}, "me@example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "own-address")

    def test_auto_submitted(self):
        gw = _make_gateway()
        ok, reason = gw._should_process(
            self._parsed(auto_submitted="auto-replied"), {}, "me@example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "auto-submitted")

    def test_precedence_bulk(self):
        gw = _make_gateway()
        ok, _ = gw._should_process(
            self._parsed(precedence="bulk"), {}, "me@example.com")
        self.assertFalse(ok)

    def test_no_from(self):
        gw = _make_gateway()
        ok, _ = gw._should_process(self._parsed(**{"from": ""}), {}, "me@example.com")
        self.assertFalse(ok)


class SmtpTests(unittest.TestCase):
    CFG = {
        "imap_host": "i", "imap_user": "u", "imap_pass": "p",
        "smtp_host": "smtp.example.com", "smtp_user": "me@example.com",
        "smtp_pass": "secret",
    }

    def test_send_ssl(self):
        gw = _make_gateway()
        with patch.object(gw, "_merged_config", return_value=self.CFG), \
             patch("smtplib.SMTP_SSL") as mock_ssl:
            server = mock_ssl.return_value  # __enter__ returns self in smtplib
            mid = gw.send_email({}, "bob@example.com", "Subj", "Body")
            server.login.assert_called_once_with("me@example.com", "secret")
            server.send_message.assert_called_once()
            self.assertTrue(mid)  # Message-ID generated

    def test_send_invalid_to(self):
        gw = _make_gateway()
        with patch.object(gw, "_merged_config", return_value=self.CFG):
            with self.assertRaises(ValueError):
                gw.send_email({}, "not-an-email", "s", "b")

    def test_send_in_reply_to(self):
        gw = _make_gateway()
        with patch.object(gw, "_merged_config", return_value=self.CFG), \
             patch("smtplib.SMTP_SSL") as mock_ssl:
            server = mock_ssl.return_value  # __enter__ returns self in smtplib
            gw.send_email({}, "b@example.com", "s", "b", in_reply_to="<orig>")
            sent_msg = server.send_message.call_args[0][0]
            self.assertEqual(sent_msg["In-Reply-To"], "<orig>")

    def test_tool_send_no_config(self):
        gw = _make_gateway()
        with patch.object(gw, "_merged_config", return_value={}):
            out = gw.tool_send("b@example.com", "s", "b")
            self.assertTrue(out.startswith("ERROR:"))

    def test_send_sets_auto_submitted(self):
        # E1: outgoing mail must carry Auto-Submitted so two Zeline
        # instances can never ping-pong auto-replies (RFC 3834 §3.1.8).
        gw = _make_gateway()
        with patch.object(gw, "_merged_config", return_value=self.CFG), \
             patch("smtplib.SMTP_SSL") as mock_ssl:
            server = mock_ssl.return_value
            gw.send_email({}, "b@example.com", "s", "b")
            sent_msg = server.send_message.call_args[0][0]
            self.assertEqual(sent_msg["Auto-Submitted"], "auto-replied")


class ProcessUnseenSizeTests(unittest.TestCase):
    # E2: oversized messages must be marked seen + skipped without ever
    # fetching the full RFC822 body (memory-DoS guard).
    CFG = {
        "imap_host": "i", "imap_user": "me@example.com", "imap_pass": "p",
        "smtp_host": "s", "smtp_user": "me@example.com", "smtp_pass": "p",
    }
    RAW = (
        b"From: alice@example.com\r\n"
        b"To: me@example.com\r\n"
        b"Subject: hi\r\n"
        b"Message-ID: <m1>\r\n"
        b"\r\n"
        b"hello world"
    )

    def test_skips_oversized_without_full_fetch(self):
        gw = _make_gateway()
        imap = MagicMock()
        big = 30 * 1024 * 1024
        imap.uid.side_effect = [
            ("OK", [b"1"]),  # search UNSEEN
            ("OK", [(f"1 (RFC822.SIZE {big} UID 1)".encode(), b"")]),  # SIZE
            ("OK", [None]),  # store \Seen
        ]
        sessions = MagicMock()
        n = gw._process_unseen(
            imap, dict(self.CFG), sessions, "safe", None, set(), "me@example.com")
        self.assertEqual(n, 0)
        sessions.send.assert_not_called()
        for call in imap.uid.call_args_list:
            if call.args[0] == "fetch":
                self.assertNotIn("(RFC822)", call.args[2],
                                 "full body must never be fetched for oversized mail")
        store_calls = [c for c in imap.uid.call_args_list if c.args[0] == "store"]
        self.assertTrue(store_calls, "oversized mail must be marked seen")

    def test_fetches_under_cap(self):
        gw = _make_gateway()
        imap = MagicMock()
        imap.uid.side_effect = [
            ("OK", [b"1"]),
            ("OK", [(b"1 (RFC822.SIZE 128 UID 1)", b"")]),
            ("OK", [(b"1 (RFC822 {128}", self.RAW)]),
            ("OK", [None]),
        ]
        sessions = MagicMock()
        sessions.send.return_value = "thanks!"
        with patch.object(gw, "send_email", return_value="<mid>") as mock_send:
            n = gw._process_unseen(
                imap, dict(self.CFG), sessions, "safe", None, set(), "me@example.com")
        self.assertEqual(n, 1)
        sessions.send.assert_called_once()
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["in_reply_to"], "<m1>")

    def test_size_unknown_fails_closed(self):
        gw = _make_gateway()
        imap = MagicMock()
        imap.uid.side_effect = [
            ("OK", [b"1"]),
            ("OK", [None]),  # SIZE fetch answered nothing usable
            ("OK", [None]),  # store \Seen
        ]
        sessions = MagicMock()
        n = gw._process_unseen(
            imap, dict(self.CFG), sessions, "safe", None, set(), "me@example.com")
        self.assertEqual(n, 0)
        sessions.send.assert_not_called()

    def test_max_fetch_bytes_config(self):
        gw = _make_gateway()
        self.assertEqual(gw.merged_cfg_max_fetch({}), 25 * 1024 * 1024)
        self.assertEqual(gw.merged_cfg_max_fetch({"max_fetch_bytes": 1024}), 1024 * 1024)  # clamped to 1MB
        self.assertEqual(gw.merged_cfg_max_fetch({"max_fetch_bytes": "bogus"}), 25 * 1024 * 1024)


class DispatchCapTests(unittest.TestCase):
    def test_dispatch_cap_20_per_cycle(self):
        # A2-M1: max 20 dispatches per _process_unseen cycle.
        from zeline.gateways import email as gw
        import inspect
        src = inspect.getsource(gw._process_unseen)
        self.assertIn("max_dispatch_per_cycle = 20", src)


class IdleTests(unittest.TestCase):
    def test_idle_new_mail(self):
        gw = _make_gateway()
        imap = MagicMock()
        imap._new_tag.return_value = b"A1"
        # readline sequence: continuation, then EXISTS
        imap.readline.side_effect = [b"+ idling\r\n", b"* 3 EXISTS\r\n"]
        imap.sock.gettimeout.return_value = None
        self.assertTrue(gw._idle_wait(imap, 10))
        # DONE was sent
        sent = b"".join(c.args[0] for c in imap.send.call_args_list)
        self.assertIn(b"DONE", sent)

    def test_idle_timeout(self):
        gw = _make_gateway()
        import socket as _socket
        imap = MagicMock()
        imap._new_tag.return_value = b"A1"
        imap.readline.side_effect = [b"+ idling\r\n", _socket.timeout()]
        imap.sock.gettimeout.return_value = None
        self.assertFalse(gw._idle_wait(imap, 10))

    def test_idle_unsupported(self):
        gw = _make_gateway()
        imap = MagicMock()
        imap._new_tag.return_value = b"A1"
        imap.readline.side_effect = [b"A1 NO IDLE not supported\r\n"]
        self.assertFalse(gw._idle_wait(imap, 10))

    def test_idle_sock_none_no_crash(self):
        # E3: imap.sock may be None if the connection dropped — must not
        # raise AttributeError, just report no mail.
        gw = _make_gateway()
        imap = MagicMock()
        imap._new_tag.return_value = b"A1"
        imap.readline.side_effect = [b"+ idling\r\n"]
        imap.sock = None
        self.assertFalse(gw._idle_wait(imap, 10))

    def test_remember_replied_bounded(self):
        # E4: the dedup set must not grow without limit.
        gw = _make_gateway()
        s: set[str] = set()
        for i in range(gw._MAX_REPLIED_MSGIDS + 5000):
            gw._remember_replied(s, f"<m{i}@x>")
        self.assertLessEqual(len(s), gw._MAX_REPLIED_MSGIDS)


class RegistrationTests(unittest.TestCase):
    def test_gateway_registered(self):
        from zeline.gateways import GATEWAYS
        self.assertIn("email", GATEWAYS)

    def test_tool_registered(self):
        from zeline.tools import TOOL_DEFS
        names = [t.name for t in TOOL_DEFS]
        self.assertIn("email_send", names)


class AuthResultsTests(unittest.TestCase):
    """EMAIL-1: DKIM/SPF failures must be rejected (spoofed From)."""

    def _parsed(self, **kw):
        base = {
            "from": "alice@example.com", "from_header": "Alice <alice@example.com>",
            "subject": "Hi", "date": "", "message_id": "<1>",
            "auto_submitted": "", "precedence": "",
            "x_auto_response_suppress": "", "auth_results": "", "body": "hi",
        }
        base.update(kw)
        return base

    def _cfg(self):
        return {"allowed_senders": ["alice@example.com"]}

    def test_dkim_fail_rejected(self):
        gw = _make_gateway()
        p = self._parsed(auth_results="mx.example.com; dkim=fail (bad signature)")
        ok, reason = gw._should_process(p, self._cfg(), "me@example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "auth-fail")

    def test_spf_fail_rejected(self):
        gw = _make_gateway()
        p = self._parsed(auth_results="mx.example.com; spf=fail (not authorized)")
        ok, reason = gw._should_process(p, self._cfg(), "me@example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "auth-fail")

    def test_dkim_pass_accepted(self):
        gw = _make_gateway()
        p = self._parsed(auth_results="mx.example.com; dkim=pass spf=pass")
        ok, _ = gw._should_process(p, self._cfg(), "me@example.com")
        self.assertTrue(ok)

    def test_missing_auth_results_accepted(self):
        # Not all MTAs add the header — absence is "no data", not "fail".
        gw = _make_gateway()
        p = self._parsed(auth_results="")
        ok, _ = gw._should_process(p, self._cfg(), "me@example.com")
        self.assertTrue(ok)

    def test_parse_extracts_auth_results(self):
        gw = _make_gateway()
        raw = (
            b"From: alice@example.com\r\n"
            b"Authentication-Results: mx.example.com; dkim=fail\r\n"
            b"Subject: t\r\nMessage-ID: <x>\r\n"
            b"Content-Type: text/plain\r\n\r\nbody\r\n"
        )
        p = gw._parse_message(raw)
        self.assertIn("dkim=fail", p["auth_results"])

    def test_injection_filter_applied_before_send(self):
        # EMAIL-1: the agent text must pass through injection_filter.
        import inspect
        from zeline.gateways import email as gw
        src = inspect.getsource(gw._process_unseen)
        self.assertIn("injection_filter.filter_tool_result", src)

    def test_docstring_warns_about_from_spoofing(self):
        from zeline.gateways import email as gw
        doc = (gw.__doc__ or "").lower()
        self.assertIn("spoof", doc)
        self.assertIn("allowed_senders", doc)


if __name__ == "__main__":
    unittest.main()
