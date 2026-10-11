"""Tests for meeting agents (zeline/meeting.py). All mocked — no real browser."""
import json
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from zeline import meeting as m


class FakeSession:
    """Minimal BrowserSession double."""

    def __init__(self, page_text="", captions=""):
        self.page_text = page_text
        self.captions = captions
        self.opened = []
        self.clicked = []
        self.stopped = False

    def start(self):
        pass

    def open(self, url, wait=3.0):
        self.opened.append(url)

    def text(self, selector="body", limit=12000):
        return self.page_text

    def evaluate(self, js):
        if "captions" in js.lower() or "caption" in js:
            return self.captions
        if "innerText" in js:
            # _click_text / _type_first_input probes
            if "button" in js:
                self.clicked.append(js)
                return True
            return True
        return ""

    def stop(self):
        self.stopped = True


def _patch_browser(monkey=None):
    """Patch meeting's browser import to return FakeSession."""
    fake_browser = mock.MagicMock()
    fake_browser.enabled.return_value = True
    fake_browser.find_browser.return_value = "/usr/bin/chromium"
    fake_browser._is_blocked_host.side_effect = lambda url: False
    fake_session = FakeSession()

    def _make_session(*a, **k):
        return fake_session

    fake_browser.BrowserSession.side_effect = _make_session
    return fake_browser, fake_session


class DetectPlatformTests(unittest.TestCase):
    def test_zoom(self):
        self.assertEqual(m.detect_platform("https://us05web.zoom.us/j/123"), "zoom")
        self.assertEqual(m.detect_platform("https://zoom.us/j/123?pwd=x"), "zoom")

    def test_meet(self):
        self.assertEqual(
            m.detect_platform("https://meet.google.com/abc-defg-hij"), "meet"
        )

    def test_teams(self):
        self.assertEqual(
            m.detect_platform("https://teams.microsoft.com/l/meetup-join/x"),
            "teams",
        )
        self.assertEqual(
            m.detect_platform("https://teams.live.com/meet/x"), "teams"
        )

    def test_unknown(self):
        self.assertEqual(m.detect_platform("https://example.com/x"), "unknown")
        self.assertEqual(m.detect_platform("not a url"), "unknown")
        self.assertEqual(m.detect_platform(""), "unknown")


class RedactUrlTests(unittest.TestCase):
    def test_pwd_redacted(self):
        out = m.redact_url("https://zoom.us/j/123?pwd=secret123")
        self.assertNotIn("secret123", out)
        self.assertIn("pwd=REDACTED", out)

    def test_other_params_kept(self):
        out = m.redact_url("https://zoom.us/j/123?pwd=s&x=1")
        self.assertIn("x=1", out)
        self.assertNotIn("pwd=s&", out)

    def test_no_query(self):
        url = "https://meet.google.com/abc"
        self.assertEqual(m.redact_url(url), url)


class ValidateUrlTests(unittest.TestCase):
    def test_rejects_http(self):
        with self.assertRaises(m.MeetingError):
            m._validate_url("http://zoom.us/j/123")

    def test_rejects_empty(self):
        with self.assertRaises(m.MeetingError):
            m._validate_url("")

    def test_rejects_unknown_platform(self):
        import zeline.browser as b

        with mock.patch.object(b, "_is_blocked_host", return_value=False):
            with self.assertRaises(m.MeetingError) as ctx:
                m._validate_url("https://some-random-site.example/j/1")
            self.assertIn("tidak dikenal", str(ctx.exception))

    def test_rejects_blocked_host(self):
        import zeline.browser as b

        with mock.patch.object(b, "_is_blocked_host", return_value=True):
            with self.assertRaises(m.MeetingError):
                m._validate_url("https://zoom.us/j/123")


class JoinMeetingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(__file__).parent / "tmp_meeting_home"
        self._tmp.mkdir(exist_ok=True)

    def _run_join(self, url, page_text=""):
        fake_browser, fake_session = _patch_browser()
        fake_session.page_text = page_text
        with mock.patch.dict("sys.modules", {"zeline.browser": fake_browser}):
            # meeting.py does "from zeline import browser as browser_module"
            # inside the function; patch the real module attribute instead.
            import zeline.browser as real_b

            with mock.patch.object(real_b, "enabled", return_value=True), mock.patch.object(
                real_b, "find_browser", return_value="/usr/bin/chromium"
            ), mock.patch.object(real_b, "_is_blocked_host", return_value=False), mock.patch.object(
                real_b, "BrowserSession", return_value=fake_session
            ):
                with mock.patch.object(m, "_meetings_dir", return_value=self._tmp):
                    mid = m.join_meeting(url, name="Tester")
        return mid, fake_session

    def test_join_zoom_waiting(self):
        mid, sess = self._run_join(
            "https://zoom.us/j/123?pwd=secret", page_text="waiting room"
        )
        self.assertTrue(mid.startswith("mtg_"))
        self.assertTrue(sess.opened)
        # pwd must never hit the state file
        state = json.loads((self._tmp / f"{mid}.json").read_text())
        self.assertNotIn("secret", json.dumps(state))
        self.assertEqual(state["status"], "waiting")
        self.assertEqual(state["platform"], "zoom")

    def test_join_meet_joined(self):
        mid, sess = self._run_join("https://meet.google.com/abc-defg-hij")
        state = json.loads((self._tmp / f"{mid}.json").read_text())
        self.assertEqual(state["platform"], "meet")
        # no waiting markers and a join click happened -> joined
        self.assertEqual(state["status"], "joined")

    def test_state_file_0600(self):
        mid, _ = self._run_join("https://zoom.us/j/1")
        mode = stat.S_IMODE(os.stat(self._tmp / f"{mid}.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_join_no_browser(self):
        import zeline.browser as real_b

        with mock.patch.object(real_b, "enabled", return_value=True), mock.patch.object(
            real_b, "find_browser", return_value=None
        ), mock.patch.object(real_b, "_is_blocked_host", return_value=False):
            with mock.patch.object(m, "_meetings_dir", return_value=self._tmp):
                with self.assertRaises(m.MeetingError) as ctx:
                    m.join_meeting("https://zoom.us/j/1")
                self.assertIn("browser", str(ctx.exception).lower())

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)
        # Cleanup _SESSIONS — _run_join menambah session tanpa cleanup.
        with m._SESSIONS_LOCK:
            m._SESSIONS.clear()


class NotesLeaveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(__file__).parent / "tmp_meeting_home2"
        self._tmp.mkdir(exist_ok=True)
        self._patcher = mock.patch.object(m, "_meetings_dir", return_value=self._tmp)
        self._patcher.start()
        # seed a meeting state directly
        self.mid = "mtg_test123"
        m._write_json_atomic(
            self._tmp / f"{self.mid}.json",
            {
                "id": self.mid,
                "platform": "meet",
                "url": "https://meet.google.com/abc",
                "name": "",
                "status": "joined",
                "joined_at": "2026-01-01T00:00:00",
                "steps": [],
            },
        )
        (self._tmp / f"{self.mid}.md").write_text("# notes\n", encoding="utf-8")

    def tearDown(self):
        self._patcher.stop()
        m._SESSIONS.pop(self.mid, None)
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_take_notes_captions(self):
        m._SESSIONS[self.mid] = FakeSession(captions="hello world")
        entry = m.take_notes(self.mid)
        self.assertIn("hello world", entry)
        notes = (self._tmp / f"{self.mid}.md").read_text(encoding="utf-8")
        self.assertIn("hello world", notes)

    def test_take_notes_no_session(self):
        entry = m.take_notes(self.mid)
        self.assertIn("tidak aktif", entry)

    def test_take_notes_audio(self):
        with mock.patch("zeline.voice.transcribe", return_value="transkrip ok") as tr:
            entry = m.take_notes(self.mid, audio_path="/tmp/x.wav")
        tr.assert_called_once_with("/tmp/x.wav")
        self.assertIn("transkrip ok", entry)

    def test_take_notes_bad_audio(self):
        import zeline.voice as v

        with mock.patch(
            "zeline.voice.transcribe", side_effect=v.VoiceError("rusak")
        ):
            with self.assertRaises(m.MeetingError):
                m.take_notes(self.mid, audio_path="/tmp/x.wav")

    def test_leave(self):
        sess = FakeSession()
        m._SESSIONS[self.mid] = sess
        out = m.leave_meeting(self.mid)
        self.assertIn(self.mid, out)
        self.assertTrue(sess.stopped)
        state = json.loads((self._tmp / f"{self.mid}.json").read_text())
        self.assertEqual(state["status"], "left")
        # notes after leave -> error
        with self.assertRaises(m.MeetingError):
            m.take_notes(self.mid)

    def test_unknown_id(self):
        with self.assertRaises(m.MeetingError):
            m.take_notes("mtg_nope")

    def test_id_traversal_rejected(self):
        with self.assertRaises(m.MeetingError):
            m.take_notes("../../etc/passwd")

    def test_list_meetings(self):
        items = m.list_meetings()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], self.mid)
        self.assertEqual(items[0]["platform"], "meet")


if __name__ == "__main__":
    unittest.main()
