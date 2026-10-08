"""Tests for peer bot-to-bot communication (zeline/peer.py)."""

import threading
import time
import unittest

import requests

from zeline import peer as peer_mod

SECRET = "test-peer-secret-abc123"


def _start_server(secret=SECRET, name="peer-test"):
    server = peer_mod.PeerServer(host="127.0.0.1", port=0, secret=secret, name=name)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.4)
    return server, f"http://127.0.0.1:{server.port}"


class PeerAuthTests(unittest.TestCase):
    def test_health_no_auth(self):
        server, url = _start_server()
        try:
            self.assertTrue(peer_mod.check_peer_health(url))
        finally:
            server.shutdown()

    def test_send_correct_secret(self):
        peer_mod._run_peer_turn = staticmethod(
            lambda from_name, text, reply_to="": f"reply:{text}"
        )
        server, url = _start_server()
        try:
            data = peer_mod.send_to_peer(url, SECRET, "hello", from_name="tester")
            self.assertTrue(data.get("ok"))
            self.assertIn("reply:hello", data.get("response", ""))
            self.assertEqual(data.get("from"), "peer-test")
        finally:
            server.shutdown()

    def test_wrong_secret_rejected(self):
        server, url = _start_server()
        try:
            with self.assertRaises(ValueError) as ctx:
                peer_mod.send_to_peer(url, "wrong", "hello")
            self.assertIn("401", str(ctx.exception))
        finally:
            server.shutdown()

    def test_empty_secret_refused_at_construction(self):
        with self.assertRaises(ValueError):
            peer_mod.PeerServer(secret="")

    def test_bearer_auth_works(self):
        peer_mod._run_peer_turn = staticmethod(lambda fn, tx, rt="": "ok")
        server, url = _start_server()
        try:
            r = requests.post(
                url + "/peer/message",
                json={"from": "x", "text": "hi"},
                headers={"Authorization": "Bearer " + SECRET},
                timeout=5,
            )
            self.assertEqual(r.status_code, 200)
        finally:
            server.shutdown()


class PeerValidationTests(unittest.TestCase):
    def setUp(self):
        peer_mod._run_peer_turn = staticmethod(lambda fn, tx, rt="": "ok")
        self.server, self.url = _start_server()
        self.h = {"X-Zeline-Token": SECRET}

    def tearDown(self):
        self.server.shutdown()

    def test_missing_text_400(self):
        r = requests.post(self.url + "/peer/message", json={"from": "x"},
                          headers=self.h, timeout=5)
        self.assertEqual(r.status_code, 400)

    def test_invalid_json_400(self):
        r = requests.post(self.url + "/peer/message", data="not{json",
                          headers=self.h, timeout=5)
        self.assertEqual(r.status_code, 400)

    def test_oversize_body_413(self):
        r = requests.post(self.url + "/peer/message",
                          json={"from": "x", "text": "y" * 40000},
                          headers=self.h, timeout=5)
        self.assertEqual(r.status_code, 413)

    def test_empty_body_400_not_413(self):
        # P4: empty body is a client error (400), not "too large" (413).
        r = requests.post(self.url + "/peer/message", data=b"",
                          headers=self.h, timeout=5)
        self.assertEqual(r.status_code, 400)
        self.assertIn("empty body", r.json()["error"])

    def test_unknown_path_404(self):
        r = requests.post(self.url + "/nope", json={}, headers=self.h, timeout=5)
        self.assertEqual(r.status_code, 404)

    def test_rate_limited(self):
        blocked = 0
        for _ in range(40):
            r = requests.post(self.url + "/peer/message",
                              json={"from": "x", "text": "spam"},
                              headers=self.h, timeout=5)
            if r.status_code == 429:
                blocked += 1
        self.assertGreater(blocked, 0, "rate limit never triggered")

    def test_unauth_flood_is_rate_limited(self):
        # P2: the limiter runs before auth, so bad-secret floods also
        # consume budget (previously they bypassed it entirely).
        peer_mod._POST_LIMITER = peer_mod._RateLimiter(30, 60.0)
        server, url = _start_server()
        try:
            statuses = set()
            for _ in range(40):
                r = requests.post(url + "/peer/message",
                                  json={"from": "x", "text": "spam"},
                                  headers={"X-Zeline-Token": "wrong-secret"},
                                  timeout=5)
                statuses.add(r.status_code)
            self.assertIn(401, statuses, "expected some 401s")
            self.assertIn(429, statuses,
                          "unauth flood must eventually hit 429")
        finally:
            server.shutdown()


class PeerSendTests(unittest.TestCase):
    def test_send_to_peer_no_redirect_followed(self):
        # A peer that redirects must fail closed, not follow.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Redir(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/evil")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Redir)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with self.assertRaises(ValueError) as ctx:
                peer_mod.send_to_peer(url, SECRET, "hi")
            self.assertIn("redirect", str(ctx.exception).lower())
        finally:
            srv.shutdown()

    def test_send_empty_secret_raises(self):
        with self.assertRaises(ValueError):
            peer_mod.send_to_peer("http://127.0.0.1:9", "", "hi")

    def test_send_blocks_cloud_metadata_ip(self):
        # P1: direct SSRF to 169.254.169.254 must be refused before any
        # connection attempt.
        from unittest.mock import patch
        with patch("requests.post") as mock_post:
            with self.assertRaises(ValueError) as ctx:
                peer_mod.send_to_peer("http://169.254.169.254/", SECRET, "hi")
            self.assertIn("blocked", str(ctx.exception).lower())
            mock_post.assert_not_called()

    def test_send_blocks_rfc1918(self):
        from unittest.mock import patch
        with patch("requests.post") as mock_post:
            for url in ("http://10.0.0.5:8788", "http://192.168.1.10:8788",
                        "http://[fd00::1]:8788"):
                with self.assertRaises(ValueError, msg=url):
                    peer_mod.send_to_peer(url, SECRET, "hi")
            mock_post.assert_not_called()

    def test_send_allows_loopback(self):
        # Local peers are the primary use case — loopback must NOT be
        # blocked by the SSRF guard.
        self.assertFalse(peer_mod._host_is_blocked("127.0.0.1"))
        self.assertFalse(peer_mod._host_is_blocked("::1"))
        self.assertFalse(peer_mod._host_is_blocked("localhost"))

    def test_send_rejects_non_http_scheme(self):
        with self.assertRaises(ValueError):
            peer_mod.send_to_peer("ftp://example.com/x", SECRET, "hi")
        with self.assertRaises(ValueError):
            peer_mod.send_to_peer("file:///etc/passwd", SECRET, "hi")

    def test_send_to_peer_strips_path_query(self):
        # P3: caller-supplied path/query must not leak into the endpoint.
        from unittest.mock import patch, MagicMock
        with patch("requests.post") as mock_post:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"ok": True}
            mock_post.return_value = resp
            peer_mod.send_to_peer(
                "http://127.0.0.1:9999/some/path?x=1&y=2", SECRET, "hi")
            called_url = mock_post.call_args[0][0]
            self.assertEqual(called_url, "http://127.0.0.1:9999/peer/message")

    def test_generate_secret(self):
        s1 = peer_mod.generate_secret()
        s2 = peer_mod.generate_secret()
        self.assertTrue(len(s1) >= 32)
        self.assertNotEqual(s1, s2)


class PeerToolTests(unittest.TestCase):
    def test_peer_send_unknown_peer(self):
        from zeline import tools as tools_mod
        from zeline import config as cfg

        orig = getattr(cfg, "PEERS", None)
        cfg.PEERS = {}
        try:
            result = tools_mod._peer_send("ghost", "hello")
            self.assertIn("unknown peer", result)
        finally:
            cfg.PEERS = orig
    def test_check_peer_health_blocks_ssrf(self):
        from zeline import peer
        # Cloud metadata + RFC1918 blocked, zero connection attempt
        self.assertFalse(peer.check_peer_health("http://169.254.169.254/"))
        self.assertFalse(peer.check_peer_health("http://192.168.1.1/"))
        self.assertFalse(peer.check_peer_health("http://10.0.0.1:8080/"))
        # Non-http scheme rejected
        self.assertFalse(peer.check_peer_health("ftp://example.com/"))
        # Loopback passes the guard (conn refused -> False, but not blocked)
        self.assertFalse(peer.check_peer_health("http://127.0.0.1:9/"))

    def test_check_peer_health_live(self):
        import threading
        from zeline import peer
        from http.server import BaseHTTPRequestHandler, HTTPServer
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            self.assertTrue(peer.check_peer_health(f"http://127.0.0.1:{port}/"))
        finally:
            srv.shutdown()
            srv.server_close()


class PeerConcurrencyTests(unittest.TestCase):
    def test_turn_semaphore_exists(self):
        # A2-L1: PeerServer bounds concurrent peer turns.
        import threading
        from zeline import peer
        srv = peer.PeerServer(secret="x" * 32)
        self.assertTrue(hasattr(srv, "_turn_semaphore"))
        self.assertIsInstance(srv._turn_semaphore, type(threading.Semaphore()))

    def test_turn_semaphore_exhausted_returns_503(self):
        # A2-L1: when all 4 turn slots are held, do_POST returns 503.
        import threading
        from unittest.mock import patch
        from zeline import peer
        srv = peer.PeerServer(secret="s3cret" + "x" * 26)
        srv._turn_acquire_timeout = 0.2  # don't wait 30s in tests
        # Hold all permits.
        held = [srv._turn_semaphore.acquire(blocking=False) for _ in range(4)]
        self.assertTrue(all(held))
        handler_cls = srv._make_handler()
        # Fake a POST /peer/message through the handler with minimal stubbing.
        import io, json as _json
        body = _json.dumps({"text": "halo", "from": "t"}).encode()
        h = handler_cls.__new__(handler_cls)
        h.headers = {"Content-Length": str(len(body)),
                     "X-Zeline-Token": srv.secret}
        h.path = "/peer/message"
        h.rfile = io.BytesIO(body)
        h.client_address = ("127.0.0.1", 1234)
        h.wfile = io.BytesIO()
        h.requestline = "POST /peer/message HTTP/1.1"  # send_response logs it
        h.request_version = "HTTP/1.1"
        # Bypass rate limiter for this unit test.
        with patch.object(peer, "_POST_LIMITER") as lim:
            lim.allow.return_value = True
            h.do_POST()
        out = h.wfile.getvalue()
        # _json writes headers then body; find the JSON payload.
        payload = out.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in out else out
        data = _json.loads(payload.decode())
        self.assertEqual(data.get("error"), "server busy — too many concurrent peer turns")
        for _ in range(4):
            srv._turn_semaphore.release()


if __name__ == "__main__":
    unittest.main()
