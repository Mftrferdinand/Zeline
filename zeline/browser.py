"""Browser automation over the Chrome DevTools Protocol.

Why this exists: ``web_fetch`` retrieves HTML, which is useless for anything
rendered by JavaScript, behind a login, or reachable only by clicking. This
drives a real browser instead — navigate, read the rendered text, click, type,
screenshot, evaluate JS.

Three decisions worth stating, because each has a tempting wrong answer:

**No new dependency.** Zeline installs on three requirements
(requests/PyYAML/pypdf). Pulling in playwright or selenium would add a large
dependency, and often a bundled browser download, to *every* install for a
feature most users never touch. CDP is a WebSocket protocol, so this contains a
minimal client written against the stdlib (~80 lines) and speaks the protocol
directly. The browser binary itself stays the operator's own — it is discovered,
never downloaded.

**One tool, many actions.** After PR #188 made schema size a measurable cost, a
dozen separate browser_* tools would be a dozen schemas re-sent forever. One
``browser`` tool with an ``action`` argument keeps the catalogue cheap.

**Operator profiles only.** This runs a local binary and executes arbitrary JS in
whatever page is open, including any session the operator is logged into. A
public gateway must never reach it.

The browser is launched on first use and reused, because a cold start costs
seconds and an agent typically makes several calls in a row. It is launched with
a throwaway profile so it cannot touch the operator's real browser data.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from zeline import config

# Binaries to try, in order. Whatever the operator actually installed wins.
CANDIDATES = (
    "chromium-browser",
    "chromium",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "brave-browser",
    "microsoft-edge",
)

ALLOWED_PROFILES = frozenset({"workspace", "full"})

DEFAULT_TIMEOUT = 30.0
LAUNCH_TIMEOUT = 25.0
MAX_TEXT = 12_000


class BrowserError(RuntimeError):
    """Something went wrong that the model should be told about plainly."""


# --------------------------------------------------------------------------
# Minimal WebSocket client.
#
# CDP needs only text frames on a single connection, which is a small enough
# subset of RFC 6455 to implement directly rather than taking a dependency.
# --------------------------------------------------------------------------
class _WebSocket:
    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT):
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buffer = b""
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(handshake.encode())
        response = self._read_until(b"\r\n\r\n")
        if b"101" not in response.split(b"\r\n")[0]:
            raise BrowserError("the browser refused the DevTools WebSocket upgrade")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise BrowserError("the browser closed the DevTools connection")
            self._buffer += chunk
        head, _, rest = self._buffer.partition(marker)
        self._buffer = rest
        return head + marker

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise BrowserError("the browser closed the DevTools connection")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        # Client frames must be masked; the server's must not be.
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            if second & 0x80:  # a server frame should never be masked
                self._recv_exact(4)
            payload = self._recv_exact(length) if length else b""
            if opcode == 0x8:
                raise BrowserError("the browser closed the DevTools connection")
            if opcode == 0x9:  # ping -> pong, or Chrome drops us
                self._sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode in (0x1, 0x2, 0x0):
                return payload.decode("utf-8", errors="replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()


def enabled() -> bool:
    return bool(getattr(config, "BROWSER", True))


def find_browser() -> str | None:
    configured = str(getattr(config, "BROWSER_BINARY", "") or "").strip()
    if configured:
        return configured if (shutil.which(configured) or Path(configured).is_file()) else None
    for name in CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _is_blocked_host(url: str) -> bool:
    """Keep the browser off the loopback and private ranges.

    Same reasoning as web_fetch: a tool that will fetch any URL becomes an SSRF
    primitive against whatever is listening on localhost.
    """
    from zeline.tools import _is_internal_ip

    parsed = urlparse(url)
    if parsed.scheme in ("", "about", "data", "file"):
        return parsed.scheme in ("file",)
    host = parsed.hostname or ""
    return not host or _is_internal_ip(host)


class BrowserSession:
    """One headless browser plus one page, driven over CDP."""

    def __init__(self, binary: str | None = None, headless: bool = True):
        self.binary = binary or find_browser()
        if not self.binary:
            raise BrowserError(
                "no Chromium/Chrome binary found. Install one (Termux: "
                "`pkg install chromium`) or set tools.browser_binary in the config."
            )
        self.headless = headless
        self._process: subprocess.Popen | None = None
        self._profile: str | None = None
        self._ws: _WebSocket | None = None
        self._port = 0
        self._message_id = 0
        self.current_url = ""

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._ws is not None:
            return
        self._port = _free_port()
        self._profile = tempfile.mkdtemp(prefix="zeline-browser.")
        argv = [
            str(self.binary),
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={self._profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            # Termux and most containers have no usable sandbox; without this
            # Chromium exits immediately with no useful message.
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "about:blank",
        ]
        if self.headless:
            argv.insert(1, "--headless=new")
        self._process = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        target = self._await_target()
        self._ws = _WebSocket(target)
        self._send("Page.enable")
        self._send("Runtime.enable")

    def _await_target(self) -> str:
        """Poll the HTTP endpoint until a page target exists."""
        deadline = time.time() + LAUNCH_TIMEOUT
        last_error = "timed out"
        while time.time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise BrowserError(
                    f"the browser exited immediately (code {self._process.returncode}). "
                    "On Termux or in a container this usually means it needs --no-sandbox "
                    "or a package it is missing."
                )
            try:
                with urlopen(f"http://127.0.0.1:{self._port}/json/list", timeout=2) as handle:
                    targets = json.loads(handle.read().decode("utf-8"))
                for item in targets:
                    if item.get("type") == "page" and item.get("webSocketDebuggerUrl"):
                        return str(item["webSocketDebuggerUrl"])
                last_error = "no page target appeared"
            except (OSError, ValueError) as exc:
                last_error = exc.__class__.__name__
            time.sleep(0.25)
        self.stop()
        raise BrowserError(f"the browser did not become ready ({last_error})")

    def stop(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.terminate()
                self._process.wait(timeout=5)
            if self._process.poll() is None:
                with contextlib.suppress(Exception):
                    self._process.kill()
            self._process = None
        if self._profile:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None

    @property
    def running(self) -> bool:
        return self._ws is not None and self._process is not None and self._process.poll() is None

    # -- protocol ----------------------------------------------------------
    def _send(self, method: str, params: dict[str, Any] | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        if self._ws is None:
            raise BrowserError("the browser is not running")
        self._message_id += 1
        message_id = self._message_id
        self._ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            # CDP interleaves events with replies, so skip anything unaddressed.
            message = json.loads(self._ws.recv())
            if message.get("id") != message_id:
                continue
            if "error" in message:
                raise BrowserError(str(message["error"].get("message", "CDP error")))
            return message.get("result", {})
        raise BrowserError(f"{method} timed out after {timeout:.0f}s")

    def evaluate(self, expression: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
        result = self._send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        details = result.get("exceptionDetails")
        if details:
            text = details.get("exception", {}).get("description") or details.get("text", "error")
            raise BrowserError(f"JavaScript error: {str(text).splitlines()[0]}")
        return result.get("result", {}).get("value")

    # -- actions -----------------------------------------------------------
    def open(self, url: str, wait: float = 3.0) -> str:
        if not urlparse(url).scheme:
            url = f"https://{url}"
        if _is_blocked_host(url):
            raise BrowserError("that URL points at an internal or local address and is blocked.")
        self.start()
        self._send("Page.navigate", {"url": url})
        self._settle(wait)
        self.current_url = str(self.evaluate("location.href") or url)
        title = self.evaluate("document.title") or ""
        return f"Opened {self.current_url}\nTitle: {title}"

    def _settle(self, wait: float) -> None:
        """Wait for the document to be ready, then briefly for scripts to paint."""
        deadline = time.time() + max(0.5, wait)
        while time.time() < deadline:
            with contextlib.suppress(BrowserError):
                if self.evaluate("document.readyState", timeout=5) in ("interactive", "complete"):
                    break
            time.sleep(0.2)
        time.sleep(0.4)

    def text(self, selector: str = "body", limit: int = MAX_TEXT) -> str:
        self.start()
        script = (
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " if (!el) return null;"
            " return el.innerText || el.textContent || ''; })()"
        )
        value = self.evaluate(script)
        if value is None:
            return f"ERROR: nothing matched selector {selector!r} on {self.current_url or 'the current page'}."
        text = " \n".join(line.strip() for line in str(value).splitlines() if line.strip())
        if len(text) > limit:
            return text[:limit] + f"\n... [truncated {len(text) - limit} characters]"
        return text or "(the page rendered no visible text)"

    def click(self, selector: str) -> str:
        self.start()
        script = (
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " if (!el) return 'missing';"
            " el.scrollIntoView({block: 'center'}); el.click(); return 'ok'; })()"
        )
        if self.evaluate(script) != "ok":
            return f"ERROR: nothing matched selector {selector!r}, so there was nothing to click."
        self._settle(1.5)
        self.current_url = str(self.evaluate("location.href") or self.current_url)
        return f"Clicked {selector}. Now at {self.current_url}"

    def type(self, selector: str, text: str, submit: bool = False) -> str:
        self.start()
        # Set the value, then fire the events a framework listens for; assigning
        # .value alone leaves React and Vue unaware anything changed.
        script = (
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " if (!el) return 'missing';"
            f" el.focus(); el.value = {json.dumps(text)};"
            " el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new Event('change', {bubbles: true}));"
            " return 'ok'; })()"
        )
        if self.evaluate(script) != "ok":
            return f"ERROR: nothing matched selector {selector!r}, so there was nothing to type into."
        if submit:
            self._send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
            self._send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
            self._settle(2.5)
            self.current_url = str(self.evaluate("location.href") or self.current_url)
            return f"Typed into {selector} and pressed Enter. Now at {self.current_url}"
        return f"Typed {len(text)} character(s) into {selector}."

    def screenshot(self, path: str, workspace: Path) -> str:
        from zeline.tools import _resolve_workspace_path

        self.start()
        target = _resolve_workspace_path(path, workspace)
        if target.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            target = target.with_suffix(".png")
        result = self._send("Page.captureScreenshot", {"format": "png"}, timeout=45)
        data = result.get("data")
        if not data:
            return "ERROR: the browser returned no image data."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data))
        return f"Saved a screenshot of {self.current_url or 'the current page'} to {target}"

    def links(self, limit: int = 40) -> str:
        self.start()
        script = (
            "(() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => ({text: (a.innerText||'').trim().slice(0,60), href: a.href}))"
            f".filter(l => l.text && l.href.startsWith('http')).slice(0, {max(1, limit)}))()"
        )
        found = self.evaluate(script) or []
        if not found:
            return "(no links on the page)"
        return "\n".join(f"- {item['text']} -> {item['href']}" for item in found)
