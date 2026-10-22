"""Read-only, credential-free loopback 9Router discovery (no lifecycle actions)."""

import http.client
import io
import ipaddress
import re
import select
import socket
import time
from urllib.parse import urlsplit

_BUDGET = 1.0  # One deadline for connect, headers and body; below the 1.5s UI budget.
_MAX_RESPONSE = 32768


def _candidate(base_url: str) -> tuple[str, int]:
    """Never resolve configured hostnames: localhost is mapped to a numeric IP."""
    try:
        url = urlsplit(base_url)
        host = url.hostname or ""
        if url.scheme != "http":
            return "127.0.0.1", 20128
        if host.lower() == "localhost":
            host = "127.0.0.1"
        if ipaddress.ip_address(host).is_loopback:
            return host, url.port or 80
    except (ValueError, TypeError):
        pass
    return "127.0.0.1", 20128


class _BufferedSocket:
    def __init__(self, data: bytes):
        self.data = data

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.data)


def _fetch_path(host: str, port: int, deadline: float, path: str) -> tuple[int, str, bytes, str]:
    """Bound wire bytes and wall time, including slow-drip HTTP headers/body.

    Read into a bounded buffer before parsing: HTTPResponse's normal socket reads
    can reset per-read timeouts and therefore exceed a total deadline. No proxy,
    DNS, cookies, authorization or environment credentials are used.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    wire_host = f"[{host}]" if family == socket.AF_INET6 else host
    with socket.socket(family, socket.SOCK_STREAM) as conn:
        conn.settimeout(max(0.001, deadline - time.monotonic()))
        conn.connect((host, port))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        conn.settimeout(remaining)
        conn.sendall((f"GET {path} HTTP/1.1\r\nHost: {wire_host}:{port}\r\n"
                      "Connection: close\r\nAccept: text/html\r\n\r\n").encode("ascii"))
        conn.setblocking(False)
        data = bytearray()
        while len(data) < _MAX_RESPONSE:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([conn], [], [], remaining)[0]:
                raise TimeoutError
            chunk = conn.recv(min(4096, _MAX_RESPONSE - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    response = http.client.HTTPResponse(_BufferedSocket(bytes(data)))
    try:
        response.begin()
        return (response.status, response.getheader("Content-Type", ""),
                response.read(_MAX_RESPONSE), response.getheader("Location", ""))
    finally:
        response.close()


def _fetch_root(host: str, port: int, deadline: float) -> tuple[int, str, bytes]:
    """Follow at most two relative same-origin hops within the original deadline.

    9Router redirects / -> /dashboard -> /login when its dashboard is locked.
    Login-page metadata is public; no authentication or cookies are attempted.
    Absolute/protocol-relative redirects and query strings are never followed.
    """
    path = "/"
    for _ in range(3):
        code, content_type, body, location = _fetch_path(host, port, deadline, path)
        if code not in (301, 302, 303, 307, 308):
            break
        target = urlsplit(location)
        if target.scheme or target.netloc or not target.path.startswith("/"):
            break
        path = target.path
    return code, content_type, body


def detect_9router(base_url: str = "", provider_name: str = "") -> list[tuple[str, str]]:
    """Return UI facts independently of model-provider identity/configuration.

    A configured plain-HTTP loopback endpoint overrides the default candidate;
    remote/invalid endpoints never get probed. A provider name proves nothing.
    Only bounded relative same-origin redirects are followed. Authentication
    challenges and unrecognised HTML remain explicitly unverified.
    """
    deadline = time.monotonic() + _BUDGET
    host, port = _candidate(base_url)
    display_host = f"[{host}]" if ":" in host else host
    endpoint = f"http://{display_host}:{port}"
    status = "Not detected (unavailable)"
    try:
        code, content_type, body = _fetch_root(host, port, deadline)
        if code in (401, 403):
            status = "Not detected (authentication required; identity unverified)"
        else:
            status = "Not detected (HTTP reachable; identity unverified)"
            if 200 <= code < 300 and "text/html" in content_type.lower():
                title = re.search(rb"<title\b[^>]*>(.*?)</title\s*>", body,
                                  re.IGNORECASE | re.DOTALL)
                if title and re.search(rb"\b9router\b", title.group(1), re.IGNORECASE):
                    status = "Detected (verified HTML title)"
    except (OSError, ValueError, http.client.HTTPException):
        # Never expose exception messages: URLs or credentials may be embedded.
        pass
    return [("9Router", status), ("9Router URL", endpoint)]
