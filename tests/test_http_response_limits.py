"""Bounded HTTP previews; no real provider, secrets, or external network."""
import importlib
import unittest
from unittest import mock

import requests


class CountingRaw:
    """Represent a large body without allocating that body."""

    def __init__(self, length=32 * 1024 * 1024):
        self.remaining = length
        self.bytes_read = 0
        self.closed = False

    def read(self, amount, decode_content=False):
        if not 0 < amount <= 4096:
            raise AssertionError("Unbounded or oversized raw read")
        count = min(amount, self.remaining)
        self.remaining -= count
        self.bytes_read += count
        return b"x" * count

    def close(self):
        self.closed = True

    def release_conn(self):
        pass


def response(raw, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.reason = "OK"
    result.headers.update(headers or {"Content-Type": "text/plain; charset=utf-8"})
    result.encoding = "utf-8"
    result.raw = raw
    result.url = "https://fixture.example/api"
    return result


def bytes_response(data, headers=None):
    import io

    class Raw(io.BytesIO):
        def read(self, amount, decode_content=False):
            assert not decode_content and 0 < amount <= 4096
            return super().read(amount)

    return response(Raw(data), headers=headers)


class HttpResponseLimitTests(unittest.TestCase):
    def test_native_large_body_is_streamed_bounded_and_closed(self):
        tools = importlib.import_module("zeline.tools")
        raw = CountingRaw()
        fixture = response(raw)

        def request(*args, **kwargs):
            self.assertIs(kwargs.get("stream"), True, "HTTP eagerly buffers the body")
            return fixture

        with mock.patch.object(tools, "_is_internal_ip", return_value=False), mock.patch.object(
            tools.requests, "request", side_effect=request
        ):
            result = tools._http_request("GET", fixture.url)
        self.assertIn("Status: 200", result)
        self.assertIn("[truncated]", result)
        self.assertLessEqual(raw.bytes_read, 65537)
        self.assertTrue(raw.closed)
        self.assertLessEqual(len(result.split("\n\n", 1)[1]), 8050)

    def test_compressed_body_is_decoded_with_a_hard_output_cap(self):
        import gzip
        import io
        from zeline.http_body import read_preview, MAX_BODY_BYTES

        class CompressedRaw(CountingRaw):
            def __init__(self):
                super().__init__()
                self.data = io.BytesIO(gzip.compress(b"x" * 2_000_000))

            def read(self, amount, decode_content=False):
                assert not decode_content and 0 < amount <= 4096
                chunk = self.data.read(amount)
                self.bytes_read += len(chunk)
                return chunk

        raw = CompressedRaw()
        text, cut = read_preview(response(raw, headers={"Content-Encoding": "gzip"}))
        self.assertTrue(cut)
        self.assertEqual(len(text), MAX_BODY_BYTES)
        self.assertEqual(text, "x" * MAX_BODY_BYTES)

    def test_redirect_with_huge_declared_body_is_never_drained(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/done")
                    self.send_header("Content-Length", "2147483648")
                    self.end_headers()
                    self.wfile.write(b"not an entire redirect body")
                    self.wfile.flush()
                    release.wait(1)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "4")
                    self.end_headers()
                    self.wfile.write(b"done")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        tools = importlib.import_module("zeline.tools")
        try:
            with mock.patch.object(tools, "_is_internal_ip", return_value=False), mock.patch.object(
                tools, "WEB_TIMEOUT", 0.2
            ), mock.patch.dict("os.environ", {"NO_PROXY": "127.0.0.1"}):
                result = tools._http_request("GET", f"http://127.0.0.1:{server.server_port}/start")
            self.assertIn("Status: 200", result)
            self.assertIn("done", result)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_cross_origin_redirect_is_closed_without_reading_or_forwarding(self):
        tools = importlib.import_module("zeline.tools")
        raw = CountingRaw()
        redirected = response(raw, 307, {"Location": "https://other.example/secret-sink"})
        with mock.patch.object(tools, "_is_internal_ip", return_value=False), mock.patch.object(
            tools.requests, "request", return_value=redirected
        ) as request:
            result = tools._http_request("POST", redirected.url, '{"Authorization":"Bearer fixture"}', "private fixture")
        self.assertIn("cross-origin", result)
        self.assertIn("blocked", result)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(raw.bytes_read, 0)
        self.assertTrue(raw.closed)

    def test_openapi_large_body_is_streamed_bounded_and_closed(self):
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {get: {operationId: getFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            raw = CountingRaw()
            fixture = response(raw)

            def request(*args, **kwargs):
                self.assertIs(kwargs.get("stream"), True, "OpenAPI eagerly buffers the body")
                return fixture

            with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                openapi.requests, "request", side_effect=request
            ):
                result = registry.call("api_fixture_getFixture", {})
            self.assertIn("Status: 200", result)
            self.assertIn("[truncated]", result)
            self.assertLessEqual(raw.bytes_read, 65537)
            self.assertTrue(raw.closed)

    def test_openapi_secret_split_by_byte_cap_is_not_exposed(self):
        import io
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        secret = "FIXTURE_SECRET_" + "s" * 1400

        class Raw(io.BytesIO):
            def read(self, amount, decode_content=False):
                return super().read(amount)

        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {get: {operationId: getFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            fixture = response(Raw(("😀" * 7900 + secret + "z" * 6000).encode("utf-8")))
            with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                openapi, "_apply_security", return_value=([secret], None)
            ), mock.patch.object(openapi.requests, "request", return_value=fixture):
                result = registry.call("api_fixture_getFixture", {})
            self.assertNotIn("FIXTURE_SECRET_", result)
            self.assertIn("[truncated]", result)

    def test_mixed_public_private_dns_answers_fail_closed(self):
        import socket
        tools = importlib.import_module("zeline.tools")
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))
                   for host in ("8.8.8.8", "127.0.0.1")]
        with mock.patch.object(tools.socket, "getaddrinfo", return_value=answers):
            self.assertTrue(tools._is_internal_ip("mixed.fixture.example"))

    def test_shared_address_space_is_not_treated_as_public(self):
        tools = importlib.import_module("zeline.tools")
        self.assertTrue(tools._is_internal_ip("100.100.100.200"))

    def test_raw_deflate_remains_supported(self):
        import zlib
        from zeline.http_body import read_preview
        encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        wire = encoder.compress(b"fixture raw deflate") + encoder.flush()
        text, cut = read_preview(bytes_response(wire, {"Content-Encoding": "deflate"}))
        self.assertEqual(text, "fixture raw deflate")
        self.assertFalse(cut)

    def test_openapi_head_remains_head_after_303(self):
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {head: {operationId: headFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            first = bytes_response(b"", {"Location": "/done"})
            first.status_code = 303
            second = bytes_response(b"")
            with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                openapi.requests, "request", side_effect=[first, second]
            ) as request:
                result = registry.call("api_fixture_headFixture", {})
            self.assertIn("Status: 200", result)
            self.assertEqual(request.call_args_list[1].args[0], "HEAD")

    def test_openapi_put_remains_put_after_301(self):
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {put: {operationId: putFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            first = bytes_response(b"", {"Location": "/done"})
            first.status_code = 301
            second = bytes_response(b"")
            with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                openapi.requests, "request", side_effect=[first, second]
            ) as request:
                result = registry.call("api_fixture_putFixture", {})
            self.assertIn("Status: 200", result)
            self.assertEqual(request.call_args_list[1].args[0], "PUT")

    def test_codec_and_truncation_boundaries(self):
        import gzip
        import zlib
        from zeline.http_body import MAX_BODY_BYTES, read_preview, format_preview
        cases = [
            (b"small", {}, "small", False),
            (b"", {"Content-Encoding": "gzip"}, "", False),
            (b"x" * MAX_BODY_BYTES, {}, "x" * MAX_BODY_BYTES, False),
            (b"x" * (MAX_BODY_BYTES + 1), {}, "x" * MAX_BODY_BYTES, True),
            (gzip.compress(b"one") + gzip.compress(b"two"), {"Content-Encoding": "gzip"}, "onetwo", False),
            (zlib.compress(b"wrapped deflate"), {"Content-Encoding": "deflate"}, "wrapped deflate", False),
            (b"tiny", {"Content-Length": "9999999999"}, "tiny", False),
        ]
        for payload, headers, expected, cut in cases:
            with self.subTest(coding=headers, size=len(payload)):
                text, truncated = read_preview(bytes_response(payload, headers))
                self.assertEqual(text, expected)
                self.assertEqual(truncated, cut)
        emoji = "😀" * 8001
        text, truncated = read_preview(bytes_response(emoji.encode()))
        self.assertEqual(format_preview(text, truncated), "😀" * 8000 + "\n... [truncated]")
        fixture = bytes_response(b"caf\xe9")
        fixture.encoding = "iso-8859-1"
        self.assertEqual(read_preview(fixture), ("café", False))

    def test_unsupported_and_incomplete_compression_fail_closed(self):
        from zeline.http_body import read_preview
        for coding, wire in [("br", b"untrusted"), ("gzip, deflate", b"untrusted"),
                             ("gzip", b"not gzip"), ("gzip", b"\x1f\x8b\x08")]:
            with self.subTest(coding=coding, wire=wire):
                with self.assertRaises(requests.exceptions.ContentDecodingError):
                    read_preview(bytes_response(wire, {"Content-Encoding": coding}))

    def test_compressed_wire_header_budget_is_enforced(self):
        from zeline.http_body import MAX_WIRE_BYTES, read_preview
        wire = b"\x1f\x8b\x08\x08" + b"\x00" * 6 + b"n" * (MAX_WIRE_BYTES + 100)
        fixture = bytes_response(wire, {"Content-Encoding": "gzip"})
        text, cut = read_preview(fixture)
        self.assertEqual(text, "")
        self.assertTrue(cut)
        self.assertLessEqual(fixture.raw.tell(), MAX_WIRE_BYTES + 1)

    def test_low_level_read_failure_is_reported_and_closed(self):
        from urllib3.exceptions import ReadTimeoutError
        tools = importlib.import_module("zeline.tools")
        fixture = response(CountingRaw())
        with mock.patch.object(fixture.raw, "read", side_effect=ReadTimeoutError(None, "fixture", "timeout")), mock.patch.object(
            tools, "_is_internal_ip", return_value=False
        ), mock.patch.object(tools.requests, "request", return_value=fixture):
            result = tools._http_request("GET", fixture.url)
        self.assertTrue(result.startswith("ERROR request:"))
        self.assertTrue(fixture.raw.closed)

    def test_native_same_origin_redirect_preserves_response_cookies(self):
        tools = importlib.import_module("zeline.tools")
        first = bytes_response(b"", {"Location": "/done"})
        first.status_code = 302
        first.cookies.set("sid", "fixture-cookie", domain="fixture.example", path="/")
        second = bytes_response(b"done")
        count = 0

        def request(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return first
            jar = kwargs.get("cookies")
            self.assertIsNotNone(jar, "Redirect lost the response cookie jar")
            self.assertEqual(jar.get("sid"), "fixture-cookie")
            return second

        with mock.patch.object(tools, "_is_internal_ip", return_value=False), mock.patch.object(
            tools.requests, "request", side_effect=request
        ):
            result = tools._http_request("GET", "https://fixture.example/api")
        self.assertIn("Status: 200", result)

    def test_redaction_does_not_split_complete_secret_at_new_tail_cut(self):
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        secret = "S" * 4096
        payload = (("😀" * 7000) + secret + ("z" * 10000)).encode()
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {get: {operationId: getFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            fixture = bytes_response(payload)
            with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                openapi.requests, "request", return_value=fixture
            ), mock.patch.object(openapi, "_apply_security", return_value=([secret], None)):
                result = registry.call("api_fixture_getFixture", {})
            self.assertIn("Status: 200", result)
            self.assertNotIn("S", result.split("\n\n", 1)[1])

    def test_invalid_url_authority_returns_error_not_exception(self):
        tools = importlib.import_module("zeline.tools")
        for url in ["https://fixture.example:notaport/", "https://fixture.example:99999/", "https://[bad/"]:
            with self.subTest(url=url), mock.patch.object(tools, "_is_internal_ip", return_value=False), mock.patch.object(
                tools.requests, "request"
            ) as request:
                result = tools._http_request("GET", url)
                self.assertTrue(result.startswith("ERROR"))
                request.assert_not_called()

    def test_redaction_union_and_multibyte_tail_boundaries(self):
        from zeline.http_body import redact_preview
        cases = [
            ("ABABA", ["ABA", "BAB"], False, "[REDACTED]"),
            ("before SECRET after", ["SECRET"], False, "before [REDACTED] after"),
            ("prefixABC�", ["ABCD"], True, "prefix[REDACTED]"),
            ("text", [], True, "text"),
            ("short", ["much-longer-secret"], True, "[REDACTED]"),
        ]
        for text, secrets, cut, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(redact_preview(text, secrets, cut), expected)


    def test_openapi_invalid_url_authority_returns_error_not_exception(self):
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example:notaport}]
paths: {/api: {get: {operationId: getFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            with mock.patch.object(openapi.requests, "request") as request:
                result = registry.call("api_fixture_getFixture", {})
            self.assertTrue(result.startswith("ERROR: OpenAPI operation resolved"))
            request.assert_not_called()

    def test_openapi_same_origin_redirect_preserves_response_cookies(self):
        import tempfile
        from pathlib import Path
        openapi = importlib.import_module("zeline.openapi_tools")
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home)
            (directory / "fixture.yaml").write_text("""openapi: 3.0.3
info: {title: Fixture, version: 1.0.0}
servers: [{url: https://fixture.example}]
paths: {/api: {get: {operationId: getFixture}}}
""")
            registry = openapi.OpenApiRegistry("full", directory)
            first = bytes_response(b"", {"Location": "/done"})
            first.status_code = 302
            first.cookies.set("sid", "fixture-cookie", domain="fixture.example", path="/")
            second = bytes_response(b"done")
            calls = []

            def request(*args, **kwargs):
                calls.append(kwargs)
                return first if len(calls) == 1 else second

            with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                openapi.requests, "request", side_effect=request
            ):
                result = registry.call("api_fixture_getFixture", {})
            self.assertIn("Status: 200", result)
            self.assertEqual(calls[1]["cookies"].get("sid"), "fixture-cookie")

    def test_openapi_redirect_cookie_scope_is_preserved(self):
        import tempfile
        from pathlib import Path
        import requests
        openapi = importlib.import_module("zeline.openapi_tools")
        cases = [
            ("http", "/", True, None, False),
            ("https", "/private", False, None, False),
            ("https", "/", False, 1, False),
            ("https", "/", True, None, True),
        ]
        for scheme, path, secure, expires, expected in cases:
            with self.subTest(scheme=scheme, path=path, secure=secure, expires=expires), tempfile.TemporaryDirectory() as home:
                directory = Path(home)
                (directory / "fixture.yaml").write_text(
                    "openapi: 3.0.3\ninfo: {title: Fixture, version: 1.0.0}\n"
                    f"servers: [{{url: {scheme}://fixture.example}}]\n"
                    "paths: {/api: {get: {operationId: getFixture}}}\n"
                )
                registry = openapi.OpenApiRegistry("full", directory)
                first = bytes_response(b"", {"Location": "/public"})
                first.status_code = 302
                first.cookies.set("sid", "fixture-cookie", domain="fixture.example",
                                  path=path, secure=secure, expires=expires)
                second = bytes_response(b"done")
                prepared = []

                def request(method, url, **kwargs):
                    prepared.append(requests.Request(method, url, cookies=kwargs["cookies"]).prepare())
                    return first if len(prepared) == 1 else second

                with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
                    openapi.requests, "request", side_effect=request
                ):
                    result = registry.call("api_fixture_getFixture", {})
                self.assertIn("Status: 200", result)
                self.assertEqual("sid=fixture-cookie" in prepared[1].headers.get("Cookie", ""), expected)
