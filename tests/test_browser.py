"""Contract tests for CDP browser automation.

Live-browser tests are opt-in via ``ZELINE_BROWSER_TESTS=1``. CI runners do ship
Chrome, but launching one per test would make the suite slow and dependent on
graphics stacks that differ per platform — a browser failing to start on a
Windows runner would look like a Zeline bug. The real end-to-end run is done
against an actual browser locally; what is pinned here without one is everything
that must hold regardless: the WebSocket framing, SSRF blocking, action routing,
profile gating, session reuse, and every error path the model can hit.
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

LIVE = os.environ.get("ZELINE_BROWSER_TESTS") == "1"


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    browser = importlib.import_module("zeline.browser")
    tools = importlib.import_module("zeline.tools")
    return cfg, browser, tools


class BrowserBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self.home.mkdir(parents=True)
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.browser, self.tools = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class WebSocketFrameTests(BrowserBase):
    """The hand-written client is the risky part, so its framing is pinned.

    A tiny in-process server speaks just enough of RFC 6455 to prove the client
    handshakes, masks what it sends, and reassembles what it receives -- including
    the two extended length forms and a ping it must answer.
    """

    def _serve(self, script):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        captured: dict = {}

        def run():
            conn, _ = listener.accept()
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            captured["request"] = request
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
            )
            script(conn, captured)
            conn.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return port, captured, thread

    @staticmethod
    def _frame(payload: bytes, opcode: int = 0x1) -> bytes:
        import struct

        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        return bytes(header) + payload

    def test_handshake_and_masked_send_and_receive(self):
        def script(conn, captured):
            data = b""
            while len(data) < 2:
                data += conn.recv(4096)
            captured["client_frame"] = data
            conn.sendall(self._frame(b'{"ok":true}'))

        port, captured, thread = self._serve(script)
        ws = self.browser._WebSocket(f"ws://127.0.0.1:{port}/devtools/page/x", timeout=5)
        ws.send('{"hello":"world"}')
        self.assertEqual(json.loads(ws.recv()), {"ok": True})
        ws.close()
        thread.join(timeout=5)
        self.assertIn(b"Sec-WebSocket-Key", captured["request"])
        self.assertIn(b"Upgrade: websocket", captured["request"])
        # Client frames MUST be masked or Chrome closes the connection.
        self.assertTrue(captured["client_frame"][1] & 0x80)

    def test_medium_and_large_payloads_reassemble(self):
        for size in (200, 70_000):
            with self.subTest(size=size):
                body = json.dumps({"data": "x" * size}).encode()

                def script(conn, captured, body=body):
                    conn.recv(4096)
                    conn.sendall(self._frame(body))

                port, _, thread = self._serve(script)
                ws = self.browser._WebSocket(f"ws://127.0.0.1:{port}/x", timeout=10)
                ws.send("{}")
                self.assertEqual(len(json.loads(ws.recv())["data"]), size)
                ws.close()
                thread.join(timeout=5)

    def test_a_ping_is_answered_and_does_not_surface(self):
        """An unanswered ping gets the connection dropped mid-task."""
        def script(conn, captured):
            conn.recv(4096)
            conn.sendall(self._frame(b"", opcode=0x9))  # ping
            data = conn.recv(4096)
            captured["pong"] = data
            conn.sendall(self._frame(b'{"after":"ping"}'))

        port, captured, thread = self._serve(script)
        ws = self.browser._WebSocket(f"ws://127.0.0.1:{port}/x", timeout=5)
        ws.send("{}")
        self.assertEqual(json.loads(ws.recv()), {"after": "ping"})
        ws.close()
        thread.join(timeout=5)
        self.assertEqual(captured["pong"][0] & 0x0F, 0xA)  # pong opcode

    def test_a_close_frame_becomes_a_clear_error(self):
        def script(conn, captured):
            conn.recv(4096)
            conn.sendall(self._frame(b"", opcode=0x8))

        port, _, thread = self._serve(script)
        ws = self.browser._WebSocket(f"ws://127.0.0.1:{port}/x", timeout=5)
        ws.send("{}")
        with self.assertRaises(self.browser.BrowserError):
            ws.recv()
        thread.join(timeout=5)

    def test_a_refused_upgrade_is_reported(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def run():
            conn, _ = listener.accept()
            while b"\r\n\r\n" not in conn.recv(4096):
                pass
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            conn.close()

        threading.Thread(target=run, daemon=True).start()
        with self.assertRaises(self.browser.BrowserError):
            self.browser._WebSocket(f"ws://127.0.0.1:{port}/x", timeout=5)


class SafetyTests(BrowserBase):
    def test_loopback_and_private_urls_are_blocked(self):
        """A tool that fetches any URL is otherwise an SSRF primitive."""
        for url in (
            "http://127.0.0.1:8080/admin",
            "http://localhost/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.browser._is_blocked_host(url), url)

    def test_public_urls_are_allowed(self):
        for url in ("https://example.com", "http://example.org/path"):
            with self.subTest(url=url):
                self.assertFalse(self.browser._is_blocked_host(url))

    def test_open_refuses_a_blocked_url_without_launching(self):
        session = self.browser.BrowserSession(binary="/nonexistent/browser")
        with self.assertRaises(self.browser.BrowserError) as caught:
            session.open("http://127.0.0.1:9/")
        self.assertIn("internal", str(caught.exception))

    def test_a_missing_binary_says_how_to_fix_it(self):
        with (
            mock.patch.object(self.browser.shutil, "which", return_value=None),
            self.assertRaises(self.browser.BrowserError) as caught,
        ):
            self.browser.BrowserSession()
        message = str(caught.exception)
        self.assertIn("pkg install chromium", message)
        self.assertIn("browser_binary", message)

    def test_configured_binary_is_preferred(self):
        saved = self.config.config_copy()
        saved["tools"]["browser_binary"] = "/opt/my/chrome"
        self.config.save_config(saved)
        with mock.patch.object(self.browser.shutil, "which", return_value="/opt/my/chrome"):
            self.assertEqual(self.browser.find_browser(), "/opt/my/chrome")

    def test_disabled_by_config(self):
        saved = self.config.config_copy()
        saved["tools"]["browser"] = False
        self.config.save_config(saved)
        self.assertFalse(self.browser.enabled())


class ProtocolTests(BrowserBase):
    """Drive the session against a fake WebSocket, so no browser is needed."""

    def session(self, replies: dict[str, object]):
        session = self.browser.BrowserSession(binary="/fake/chrome")
        sent: list[dict] = []

        class FakeWS:
            def __init__(self):
                self.queue: list[str] = []

            def send(self, text):
                message = json.loads(text)
                sent.append(message)
                method = message["method"]
                value = replies.get(method, {})
                if isinstance(value, Exception):
                    body = {"id": message["id"], "error": {"message": str(value)}}
                else:
                    body = {"id": message["id"], "result": value}
                # An unrelated event first, to prove replies are matched by id.
                self.queue.append(json.dumps({"method": "Page.loadEventFired", "params": {}}))
                self.queue.append(json.dumps(body))

            def recv(self):
                return self.queue.pop(0)

            def close(self):
                pass

        session._ws = FakeWS()
        session._process = mock.Mock(poll=mock.Mock(return_value=None))
        return session, sent

    def _value(self, value):
        return {"result": {"value": value}}

    def test_events_are_skipped_while_waiting_for_a_reply(self):
        session, sent = self.session({"Runtime.evaluate": self._value("done")})
        self.assertEqual(session.evaluate("1"), "done")
        self.assertEqual(len(sent), 1)

    def test_a_cdp_error_becomes_a_browser_error(self):
        session, _ = self.session({"Runtime.evaluate": Exception("Cannot find context")})
        with self.assertRaises(self.browser.BrowserError):
            session.evaluate("1")

    def test_a_javascript_exception_is_reported_on_one_line(self):
        session, _ = self.session({"Runtime.evaluate": {
            "exceptionDetails": {"exception": {"description": "TypeError: x is not a function\n  at <anonymous>"}},
        }})
        with self.assertRaises(self.browser.BrowserError) as caught:
            session.evaluate("x()")
        message = str(caught.exception)
        self.assertIn("TypeError", message)
        self.assertNotIn("\n", message)

    def test_text_reports_a_missing_selector_instead_of_empty_output(self):
        session, _ = self.session({"Runtime.evaluate": self._value(None)})
        result = session.text("#nope")
        self.assertTrue(result.startswith("ERROR"))
        self.assertIn("#nope", result)

    def test_text_is_truncated_with_a_visible_marker(self):
        session, _ = self.session({"Runtime.evaluate": self._value("y" * 30_000)})
        result = session.text(limit=1000)
        self.assertIn("truncated", result)
        self.assertLess(len(result), 30_000)

    def test_text_says_so_when_a_page_renders_nothing(self):
        session, _ = self.session({"Runtime.evaluate": self._value("   ")})
        self.assertIn("no visible text", session.text())

    def test_click_reports_a_missing_selector(self):
        session, _ = self.session({"Runtime.evaluate": self._value("missing")})
        self.assertTrue(session.click("#gone").startswith("ERROR"))

    def test_type_fires_input_and_change_events(self):
        """Assigning .value alone leaves React and Vue unaware of the change."""
        session, sent = self.session({"Runtime.evaluate": self._value("ok")})
        session.type("#q", "hello")
        script = sent[0]["params"]["expression"]
        self.assertIn("new Event('input'", script)
        self.assertIn("new Event('change'", script)

    def test_type_escapes_the_text_it_injects(self):
        session, sent = self.session({"Runtime.evaluate": self._value("ok")})
        session.type("#q", "it's \"quoted\" and\nnewlined")
        script = sent[0]["params"]["expression"]
        # json.dumps handles the escaping; a broken quote would be a syntax error
        # in the page rather than a Python error, so it must be pinned here.
        self.assertNotIn("\n", script.split("el.value = ")[1].split(";")[0])

    def test_type_with_submit_sends_real_key_events(self):
        session, sent = self.session({
            "Runtime.evaluate": self._value("ok"),
            "Input.dispatchKeyEvent": {},
        })
        session.type("#q", "hi", submit=True)
        kinds = [m["params"].get("type") for m in sent if m["method"] == "Input.dispatchKeyEvent"]
        self.assertEqual(kinds, ["keyDown", "keyUp"])

    def test_screenshot_writes_real_bytes_into_the_workspace(self):
        import base64

        png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        session, _ = self.session({
            "Page.captureScreenshot": {"data": png},
            "Runtime.evaluate": self._value("x"),
        })
        message = session.screenshot("shots/page.png", self.home)
        target = self.home / "shots" / "page.png"
        self.assertTrue(target.is_file(), message)
        self.assertTrue(target.read_bytes().startswith(b"\x89PNG"))

    def test_screenshot_forces_an_image_extension(self):
        import base64

        session, _ = self.session({
            "Page.captureScreenshot": {"data": base64.b64encode(b"x").decode()},
            "Runtime.evaluate": self._value("x"),
        })
        session.screenshot("noext", self.home)
        self.assertTrue((self.home / "noext.png").is_file())

    def test_screenshot_cannot_escape_the_workspace(self):
        import base64

        session, _ = self.session({
            "Page.captureScreenshot": {"data": base64.b64encode(b"x").decode()},
            "Runtime.evaluate": self._value("x"),
        })
        with self.assertRaises(ValueError):
            session.screenshot("../../escaped.png", self.home)

    def test_links_are_listed_readably(self):
        session, _ = self.session({"Runtime.evaluate": self._value([
            {"text": "Docs", "href": "https://example.com/docs"},
        ])})
        self.assertIn("Docs -> https://example.com/docs", session.links())

    def test_no_links_says_so(self):
        session, _ = self.session({"Runtime.evaluate": self._value([])})
        self.assertIn("no links", session.links())


class ToolRoutingTests(BrowserBase):
    def executor(self, profile: str = "full"):
        return self.tools.ToolExecutor(
            identity="cli:local", profile=profile, workspace=str(self.home)
        )

    def test_safe_profile_cannot_browse(self):
        """It runs a local binary and executes JS in logged-in sessions."""
        result = self.executor("safe").run("browser", {"action": "open", "url": "https://example.com"})
        self.assertIn("not allowed", result)

    def test_disabled_config_is_reported(self):
        saved = self.config.config_copy()
        saved["tools"]["browser"] = False
        self.config.save_config(saved)
        result = self.executor().run("browser", {"action": "open", "url": "https://example.com"})
        self.assertIn("disabled", result)

    def test_unknown_action_lists_the_valid_ones(self):
        with mock.patch.object(self.browser, "find_browser", return_value=None):
            result = self.executor().run("browser", {"action": "teleport"})
        for verb in ("open", "text", "click", "type", "screenshot", "links", "eval", "close"):
            self.assertIn(verb, result)

    MISSING_ARG_CASES: ClassVar[dict[str, str]] = {
        "open": "needs a url",
        "click": "needs a css selector",
        "type": "needs a css selector",
        "screenshot": "needs a path",
        "eval": "needs a script",
    }

    def test_missing_arguments_are_reported_per_action(self):
        executor = self.executor()
        for action, expected in self.MISSING_ARG_CASES.items():
            with self.subTest(action=action):
                self.assertIn(expected, executor.run("browser", {"action": action}))

    def test_bad_arguments_are_reported_even_with_no_browser_installed(self):
        """CI has no Chromium, and a malformed call must still say what is wrong.

        Validating after launching meant every argument mistake was reported as
        "no browser found", which sends the model off fixing the wrong problem --
        and on a machine without a browser it hides the real mistake entirely.
        """
        executor = self.executor()
        with mock.patch.object(self.browser, "find_browser", return_value=None):
            for action, expected in self.MISSING_ARG_CASES.items():
                with self.subTest(action=action):
                    self.assertIn(expected, executor.run("browser", {"action": action}))
            self.assertIn("unknown browser action", executor.run("browser", {"action": "teleport"}))
            self.assertIn("no browser was open", executor.run("browser", {"action": "close"}))

    def test_a_valid_call_with_no_browser_still_explains_how_to_install_one(self):
        executor = self.executor()
        with mock.patch.object(self.browser, "find_browser", return_value=None):
            result = executor.run("browser", {"action": "open", "url": "https://example.com"})
        self.assertIn("no Chromium/Chrome binary found", result)

    def test_close_without_a_session_is_not_an_error(self):
        self.assertIn("no browser was open", self.executor().run("browser", {"action": "close"}))

    def test_the_session_is_reused_across_calls(self):
        """A cold start costs seconds, and agents make several calls in a row."""
        executor = self.executor()
        created = []

        class FakeSession:
            running = True

            def __init__(self):
                created.append(self)

            def open(self, url, wait=3.0):
                return f"Opened {url}"

            def text(self, selector="body", limit=0):
                return "some text"

            def stop(self):
                pass

        with mock.patch("zeline.browser.BrowserSession", FakeSession):
            executor.run("browser", {"action": "open", "url": "https://example.com"})
            executor.run("browser", {"action": "text"})
        self.assertEqual(len(created), 1)

    def test_a_dead_session_is_replaced_rather_than_reused(self):
        """Otherwise one crashed browser poisons every later call."""
        executor = self.executor()
        created = []

        class DeadSession:
            running = False

            def __init__(self):
                created.append(self)

            def open(self, url, wait=3.0):
                return f"Opened {url}"

            def stop(self):
                pass

        with mock.patch("zeline.browser.BrowserSession", DeadSession):
            executor.run("browser", {"action": "open", "url": "https://a.example"})
            executor.run("browser", {"action": "open", "url": "https://b.example"})
        self.assertEqual(len(created), 2)

    def test_a_browser_error_is_returned_as_text_not_raised(self):
        executor = self.executor()

        class FailingSession:
            running = True

            def __init__(self):
                pass

            def open(self, url, wait=3.0):
                raise self_browser.BrowserError("it exploded")

            def stop(self):
                pass

        self_browser = self.browser
        with mock.patch("zeline.browser.BrowserSession", FailingSession):
            result = executor.run("browser", {"action": "open", "url": "https://example.com"})
        self.assertTrue(result.startswith("ERROR browser:"))
        self.assertIn("it exploded", result)

    def test_the_tool_is_advertised_only_to_operator_profiles(self):
        # all_schemas, not schemas: profile membership is the question here, and
        # `schemas` may legitimately withhold a tool's detail behind tool_search.
        full = [s["function"]["name"] for s in self.executor("full").all_schemas]
        safe = [s["function"]["name"] for s in self.executor("safe").all_schemas]
        self.assertIn("browser", full)
        self.assertNotIn("browser", safe)

    def test_one_tool_covers_every_action(self):
        """Separate browser_* tools would each cost schema on every request."""
        schema = next(
            s for s in self.executor().all_schemas if s["function"]["name"] == "browser"
        )
        self.assertEqual(schema["function"]["parameters"]["required"], ["action"])


@unittest.skipUnless(LIVE, "set ZELINE_BROWSER_TESTS=1 to run against a real browser")
class LiveBrowserTests(BrowserBase):
    def test_open_read_type_click_and_screenshot(self):
        page = (
            "data:text/html,<html><body><h1 id=t>start</h1>"
            "<input id=box>"
            "<button id=b onclick=\"document.getElementById('t').innerText="
            "'clicked:'+document.getElementById('box').value\">go</button>"
            "</body></html>"
        )
        session = self.browser.BrowserSession()
        try:
            session.open(page, wait=2)
            self.assertIn("start", session.text())
            session.type("#box", "hello")
            session.click("#b")
            self.assertEqual(
                session.evaluate("document.getElementById('t').innerText"),
                "clicked:hello",
            )
            session.screenshot("live.png", self.home)
            self.assertGreater((self.home / "live.png").stat().st_size, 100)
        finally:
            session.stop()
        self.assertFalse(session.running)


if __name__ == "__main__":
    unittest.main()
