"""Small, loopback-only runtime dashboard (stdlib only)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import select
import socket
import threading
import time
from typing import Callable
from urllib.parse import quote

_ASSET_DIR = Path(__file__).with_name('runtime_assets')
_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'")
_STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
           '/index.html': ('index.html', 'text/html; charset=utf-8'),
           '/runtime.css': ('runtime.css', 'text/css; charset=utf-8'),
           '/runtime.js': ('runtime.js', 'text/javascript; charset=utf-8')}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, dashboard):
        self.dashboard = dashboard
        self.stopping = threading.Event()
        self.stream_slots = threading.BoundedSemaphore(dashboard.MAX_STREAMS)
        self.request_slots = threading.BoundedSemaphore(dashboard.MAX_HANDLERS)
        self.connections_lock = threading.Lock()
        self.connections = set()
        super().__init__(address, _Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.dashboard.WRITE_TIMEOUT)
        with self.connections_lock:
            self.connections.add(connection)
        return connection, address

    def close_request(self, request):
        with self.connections_lock:
            self.connections.discard(request)
        super().close_request(request)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def handle_error(self, request, client_address):
        # Never emit request paths, tokens, callback exceptions or tracebacks.
        pass

    def close_connections(self):
        with self.connections_lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()


class RuntimeDashboard:
    """Lifecycle owner for a local runtime dashboard."""

    STREAM_LIFETIME = 30.0
    WRITE_TIMEOUT = 2.0
    MAX_STREAMS = 4
    MAX_HANDLERS = 16

    def __init__(self, snapshot: Callable[[], dict], port: int = 8765):
        self._snapshot = snapshot
        self._port = port
        self._token = secrets.token_urlsafe(32)
        self._server = None
        self._thread = None
        self._lifecycle = threading.RLock()
        self._cache_lock = threading.Lock()
        self._cached_at = float('-inf')
        self._cached = None

    def _snapshot_bytes(self):
        # Serialized successes AND failures share a one-second cache. There is
        # no background DB poller, and clients cannot multiply callback work.
        with self._cache_lock:
            if time.monotonic() - self._cached_at >= 1.0:
                try:
                    value = self._snapshot()
                    if not isinstance(value, dict):
                        raise ValueError('snapshot must be a dict')
                    self._cached = json.dumps(value, ensure_ascii=True, allow_nan=False,
                                              separators=(',', ':')).encode('utf-8')
                except Exception:
                    self._cached = None
                self._cached_at = time.monotonic()
            return self._cached

    @property
    def url(self) -> str:
        port = self._server.server_port if self._server else self._port
        return f'http://127.0.0.1:{port}/'

    def owner_url(self) -> str:
        return self.url + '#token=' + quote(self._token, safe='')

    def start(self) -> None:
        with self._lifecycle:
            if self._server:
                return
            self._server = _Server(('127.0.0.1', self._port), self)
            self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle:
            if self._server:
                self._server.stopping.set()
                self._server.shutdown()
                self._server.close_connections()
                self._server.server_close()
                self._thread.join(timeout=2)
                self._server = None
                self._thread = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, body=b'', content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', _CSP)
        super().end_headers()

    def _events(self):
        server = self.server
        dashboard = server.dashboard
        if not server.stream_slots.acquire(blocking=False):
            self._reply(429, b'{"error":"stream limit reached"}')
            return
        try:
            body = dashboard._snapshot_bytes()
            if body is None:
                self._reply(503, b'{"error":"snapshot unavailable"}')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            deadline = time.monotonic() + dashboard.STREAM_LIFETIME
            while not server.stopping.is_set():
                self.wfile.write(b'data: ' + body + b'\n\n')
                self.wfile.flush()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or server.stopping.wait(min(1.0, remaining)):
                    break
                if time.monotonic() >= deadline:
                    break
                # A receive-side EOF/extra data ends this one-way stream before
                # collecting again; writes alone may discover closure one frame late.
                if select.select([self.connection], [], [], 0)[0]:
                    break
                body = dashboard._snapshot_bytes()
                if body is None:
                    # Headers have already been sent: terminate and reconnect,
                    # never expose callback exceptions inside an SSE frame.
                    break
        except (OSError, ValueError):
            pass
        finally:
            server.stream_slots.release()

    def _boundary(self):
        hosts = self.headers.get_all('Host', [])
        port = self.server.server_port
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if port == 80:
            allowed.update({'127.0.0.1', 'localhost'})
        origins = self.headers.get_all('Origin', [])
        if len(hosts) != 1 or hosts[0] not in allowed:
            self._reply(403)
            return False
        if origins and (len(origins) != 1 or origins[0] != 'http://' + hosts[0]):
            self._reply(403)
            return False
        if '?' in self.path or '#' in self.path:
            self._reply(400)
            return False
        return True

    def do_POST(self):
        if self._boundary():
            self._reply(405)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_POST

    def do_GET(self):
        if not self._boundary():
            return
        dashboard = self.server.dashboard
        if self.path in ('/api/runtime', '/api/events'):
            values = self.headers.get_all('Authorization', [])
            expected = 'Bearer ' + dashboard._token
            if len(values) != 1 or not secrets.compare_digest(values[0].encode(), expected.encode()):
                self._reply(401)
                return
            if self.path == '/api/events':
                self._events()
            else:
                body = dashboard._snapshot_bytes()
                if body is None:
                    self._reply(503, b'{"error":"snapshot unavailable"}')
                else:
                    self._reply(200, body)
        elif self.path == '/health':
            self._reply(200, b'{"service":"zeline-runtime"}')
        elif self.path in _STATIC:
            name, content_type = _STATIC[self.path]
            try:
                body = (_ASSET_DIR / name).read_bytes()
            except OSError:
                if name != 'index.html':
                    self._reply(503, b'{"error":"assets unavailable"}')
                    return
                body = b'<!doctype html><title>Zeline runtime</title><p>Dashboard assets unavailable.</p>'
            self._reply(200, body, content_type)
        else:
            self._reply(404)
