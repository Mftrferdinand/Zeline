"""Focused, network-isolated tests for read-only 9Router detection."""
import socket
import threading
import time
import unittest
from unittest.mock import patch

from zeline import router_probe


class RouterProbeTests(unittest.TestCase):
    def probe(self, response, url="", provider=""):
        with patch.object(router_probe, "_fetch_root", return_value=response) as fetch:
            facts = dict(router_probe.detect_9router(url, provider))
        return facts, fetch

    def test_verified_html_title(self):
        facts, fetch = self.probe((200, "text/html", b"<title>9RoUtEr - Dashboard</title>"))
        self.assertEqual(facts["9Router"], "Detected (verified HTML title)")
        self.assertEqual(facts["9Router URL"], "http://127.0.0.1:20128")
        self.assertEqual(fetch.call_args.args[:2], ("127.0.0.1", 20128))

    def test_remote_provider_does_not_change_local_detection(self):
        facts, fetch = self.probe((200, "text/html", b"<title>9router</title>"),
                                  "https://secret:token@remote.example/v1?key=secret", "OpenAI")
        self.assertIn("Detected", facts["9Router"])
        self.assertEqual(fetch.call_args.args[:2], ("127.0.0.1", 20128))
        self.assertNotIn("secret", str(facts))

    def test_configured_loopback_port_and_sanitized_url(self):
        facts, fetch = self.probe((200, "text/html", b"<title>9router</title>"),
                                  "http://user:password@localhost:12345/v1?secret=yes#token")
        self.assertEqual(fetch.call_args.args[:2], ("127.0.0.1", 12345))
        self.assertEqual(facts["9Router URL"], "http://127.0.0.1:12345")
        self.assertNotIn("password", str(facts))

    def test_ipv6_loopback(self):
        facts, fetch = self.probe((401, "", b""), "http://[::1]:20129/v1")
        self.assertEqual(fetch.call_args.args[:2], ("::1", 20129))
        self.assertEqual(facts["9Router URL"], "http://[::1]:20129")

    def test_invalid_or_non_http_or_nonlocal_configuration_uses_default(self):
        for url in ("http://[broken", "http://localhost:bad", "https://localhost:9999",
                    "http://localhost.evil:90", "http://192.168.1.1:90"):
            with self.subTest(url=url):
                _, fetch = self.probe((200, "text/html", b""), url)
                self.assertEqual(fetch.call_args.args[:2], ("127.0.0.1", 20128))

    def test_generic_models_or_mentions_are_not_identity(self):
        for content_type, body in (("application/json", b'{"data":[],"name":"9router"}'),
                                   ("text/html", b"<title>Other</title>9router"),
                                   ("text/html", b"<title>not9routerfake</title>")):
            facts, _ = self.probe((200, content_type, body), provider="9router")
            self.assertEqual(facts["9Router"], "Not detected (HTTP reachable; identity unverified)")

    def test_auth_needed_is_not_verification(self):
        for status in (401, 403):
            facts, _ = self.probe((status, "text/html", b"<title>9router</title>"))
            self.assertEqual(facts["9Router"], "Not detected (authentication required; identity unverified)")

    def test_redirect_is_not_verification(self):
        facts, fetch = self.probe((302, "text/html", b"<title>9router</title>"))
        self.assertIn("identity unverified", facts["9Router"])
        self.assertEqual(fetch.call_count, 1)

    def test_same_origin_login_redirects_are_verified_without_credentials(self):
        replies = [(307, "text/html", b"", "/dashboard"),
                   (307, "text/html", b"", "/login?never-send=secret"),
                   (200, "text/html", b"<title>9Router Login</title>", "")]
        with patch.object(router_probe, "_fetch_path", create=True, side_effect=replies) as fetch, \
             patch.object(router_probe.socket, "socket", side_effect=OSError("network disabled")):
            facts = dict(router_probe.detect_9router())
        self.assertIn("Detected", facts["9Router"])
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(fetch.call_args_list[-1].args[3], "/login")
        self.assertEqual(len({call.args[2] for call in fetch.call_args_list}), 1)
        self.assertNotIn("secret", str(facts))

    def test_redirects_never_leave_origin_or_loop_indefinitely(self):
        for location, expected_calls in (("https://external.example/", 1),
                                          ("//127.0.0.2:9999/", 1), ("/loop", 3)):
            with patch.object(router_probe, "_fetch_path", return_value=(307, "", b"", location)) as fetch:
                facts = dict(router_probe.detect_9router())
            self.assertEqual(fetch.call_count, expected_calls)
            self.assertIn("identity unverified", facts["9Router"])

    def test_errors_are_secret_free(self):
        with patch.object(router_probe, "_fetch_root", side_effect=OSError("secret-token")):
            facts = dict(router_probe.detect_9router())
        self.assertEqual(facts["9Router"], "Not detected (unavailable)")
        self.assertNotIn("secret-token", str(facts))

    def serve_once(self, handler):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(2)
        def run():
            try:
                conn, _ = server.accept()
                with conn:
                    handler(conn)
            except (OSError, TimeoutError):
                pass
            finally:
                server.close()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        return f"http://127.0.0.1:{server.getsockname()[1]}/v1?secret=hidden"

    def test_actual_http_requests_only_root_without_credentials(self):
        requests = []
        def handler(conn):
            requests.append(conn.recv(4096))
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<title>9router</title>")
        facts = dict(router_probe.detect_9router(self.serve_once(handler)))
        self.assertIn("Detected", facts["9Router"])
        self.assertTrue(requests[0].startswith(b"GET / HTTP/1.1\r\n"))
        self.assertNotIn(b"Authorization", requests[0])
        self.assertNotIn(b"secret", requests[0])

    def test_slow_drip_cannot_extend_total_deadline(self):
        def handler(conn):
            conn.recv(4096)
            for _ in range(30):
                conn.sendall(b"H")
                time.sleep(0.03)
        url = self.serve_once(handler)
        with patch.object(router_probe, "_BUDGET", 0.15):
            start = time.monotonic()
            facts = dict(router_probe.detect_9router(url))
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5)
        self.assertIn("Not detected", facts["9Router"])

    def test_response_size_cap(self):
        def handler(conn):
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 40023\r\n\r\n" +
                         b"x" * 40000 + b"<title>9router</title>")
        facts = dict(router_probe.detect_9router(self.serve_once(handler)))
        self.assertIn("identity unverified", facts["9Router"])


if __name__ == "__main__":
    unittest.main()
