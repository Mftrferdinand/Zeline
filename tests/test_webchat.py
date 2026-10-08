"""WebChat gateway — auth, endpoint, validasi, dan XSS.

Server HTTP nyata dijalankan di 127.0.0.1 dengan port ephemeral; sessions
di-mock sehingga tidak ada panggilan provider. Tidak ada akses jaringan
keluar.

unittest, bukan pytest: CI menjalankan ``python -m unittest discover`` dan
pytest tidak terpasang di sana (pytest tetap bisa menjalankan file ini).
"""

from __future__ import annotations

import json
import re
import threading
import time
import unittest
import unittest.mock
import urllib.request
import urllib.error

from zeline import approvals, interaction
from zeline.agent import ZelineError
from zeline.gateways import GATEWAYS, _validate_tool_policy, webchat


TOKEN = "webchat-test-token-16-chars-minimum-ok"


class _Sessions:
    """Mock SessionStore.send: merekam identitas dan mengembalikan gema."""

    def __init__(self, reply="balasan-mock", exc=None):
        self.calls: list[dict] = []
        self._reply = reply
        self._exc = exc

    def send(self, identity, text, tool_profile, **kwargs):
        self.calls.append({"identity": identity, "text": text, "tool_profile": tool_profile})
        if self._exc is not None:
            raise self._exc
        return self._reply


class _Server:
    """ThreadingHTTPServer webchat hidup selama satu test."""

    def __init__(self, sessions, cfg):
        cfg = dict(cfg)
        cfg["port"] = 0  # ephemeral: OS memilih port bebas per test
        self.stop_event = threading.Event()
        self.bound_port: int | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=webchat.start,
            args=(sessions, cfg, self.stop_event),
            kwargs={"ready": self._ready_cb},
            daemon=True,
        )

    def _ready_cb(self, port: int) -> None:
        self.bound_port = port
        self._ready.set()

    def __enter__(self):
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("webchat server did not become ready")
        return self

    def __exit__(self, *_exc):
        self.stop_event.set()
        self._thread.join(timeout=10)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.bound_port}{path}"

    def request(self, path, method="GET", body=None, token=TOKEN):
        data = None
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url(path), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def _cfg(**overrides):
    base = {"token": TOKEN, "host": "127.0.0.1", "port": 8787, "tool_profile": "safe"}
    base.update(overrides)
    return base


class RegistryTests(unittest.TestCase):
    def test_registered(self):
        self.assertIs(GATEWAYS["webchat"], webchat)
        self.assertEqual(webchat.info()["label"], "WebChat UI")

    def test_config_defaults_cover_webchat(self):
        from zeline import config

        missing = sorted(set(GATEWAYS) - set(config._defaults()["gateways"]))
        self.assertEqual(missing, [], f"registered gateways without defaults: {missing}")


class ValidationTests(unittest.TestCase):
    def test_token_min_16_chars(self):
        self.assertEqual(webchat.validate_config(_cfg()), [])
        errs = webchat.validate_config(_cfg(token="pendek"))
        self.assertTrue(any("16" in e for e in errs), errs)

    def test_token_must_be_ascii(self):
        # Token non-ASCII -> error jelas (auth hmac inkonsisten antar encoding).
        errs = webchat.validate_config(_cfg(token="tökén-16-karakter-üü"))
        self.assertTrue(any("ASCII" in e for e in errs), errs)
        # Token ASCII valid tidak menambah error.
        self.assertEqual(webchat.validate_config(_cfg()), [])

    def test_port_must_be_valid(self):
        self.assertTrue(any("port" in e for e in webchat.validate_config(_cfg(port=0))))
        self.assertTrue(any("port" in e for e in webchat.validate_config(_cfg(port="x"))))

    def test_tool_profile_must_stay_safe(self):
        for profile in ("workspace", "full", "bogus"):
            errs = webchat.validate_config(_cfg(tool_profile=profile))
            self.assertTrue(any("safe" in e for e in errs), (profile, errs))

    def test_tool_policy_fail_closed_for_webchat(self):
        errs = _validate_tool_policy("webchat", {"tool_profile": "workspace"})
        self.assertTrue(any("safe" in e for e in errs), errs)
        self.assertEqual(_validate_tool_policy("webchat", {"tool_profile": "safe"}), [])

    def test_empty_host_is_rejected_not_silently_bound_everywhere(self):
        # host "" di HTTPServer stdlib = bind SEMUA interface (0.0.0.0),
        # bukan loopback: harus DITOLAK (fail-closed), bukan dinormalkan
        # diam-diam. Operator wajib menulis host eksplisit.
        errs = webchat.validate_config(_cfg(host=""))
        self.assertTrue(any("host" in e and "empty" in e for e in errs), errs)
        errs = webchat.validate_config(_cfg(host="   "))
        self.assertTrue(any("host" in e and "empty" in e for e in errs), errs)
        # Host eksplisit yang valid tetap lolos.
        self.assertEqual(webchat.validate_config(_cfg(host="127.0.0.1")), [])

    def test_start_refuses_empty_host_even_without_validation(self):
        # Backstop: start() tidak boleh mengikat semua interface diam-diam
        # bila dipanggil tanpa lewat validate_config.
        with self.assertRaises(ValueError):
            webchat.start(_Sessions(), _cfg(host=""), threading.Event())


class RateLimitTests(unittest.TestCase):
    def _clock(self):
        now = [0.0]
        return now, lambda: now[0]

    def test_limiter_allows_up_to_max_then_denies(self):
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(3, 60.0, clock=clock)
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertFalse(lim.allow("1.2.3.4"))
        self.assertFalse(lim.allow("1.2.3.4"))

    def test_limiter_window_slides(self):
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(2, 60.0, clock=clock)
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertFalse(lim.allow("1.2.3.4"))
        now[0] = 61.0  # jendela berlalu -> budget penuh lagi
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertTrue(lim.allow("1.2.3.4"))
        self.assertFalse(lim.allow("1.2.3.4"))

    def test_limiter_tracks_per_key(self):
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(1, 60.0, clock=clock)
        self.assertTrue(lim.allow("10.0.0.1"))
        self.assertFalse(lim.allow("10.0.0.1"))
        # IP lain tidak terpengaruh.
        self.assertTrue(lim.allow("10.0.0.2"))

    def test_limiter_evicts_idle_keys(self):
        # MINOR-4: key yang semua hit-nya kedaluwarsa harus DIHAPUS dari
        # _hits (sesuai klaim docstring), bukan sekadar deque kosong yang
        # tertinggal. Terbukti: objek deque lama dibuang, diganti yang baru.
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(2, 60.0, clock=clock)
        self.assertTrue(lim.allow("10.9.9.9"))
        self.assertIn("10.9.9.9", lim._hits)
        old_deque = lim._hits["10.9.9.9"]
        now[0] = 61.0  # semua hit kedaluwarsa -> key tidak aktif
        self.assertTrue(lim.allow("10.9.9.9"))
        self.assertIsNot(lim._hits["10.9.9.9"], old_deque)
        self.assertEqual(list(lim._hits["10.9.9.9"]), [61.0])

    def test_limiter_caps_key_count_with_oldest_eviction(self):
        # Banjir IP unik tidak boleh membesar-besarkan tabel tanpa batas:
        # jumlah key tidak pernah melewati max_keys, dan yang dibuang
        # adalah key dengan aktivitas TERLAMA (evict oldest).
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(30, 60.0, clock=clock, max_keys=5)
        for i in range(20):
            now[0] = float(i)
            self.assertTrue(lim.allow(f"10.0.0.{i}"))
        self.assertLessEqual(len(lim._hits), 5)
        # 5 key paling segar yang bertahan; 15 key tertua dibuang.
        self.assertEqual(
            sorted(lim._hits.keys()),
            sorted(f"10.0.0.{i}" for i in range(15, 20)),
        )

    def test_limiter_evicts_long_idle_keys_first_when_full(self):
        # Saat tabel penuh dan key baru tiba, key yang paling lama idle
        # yang dikorbankan — bukan key yang baru tiba.
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(30, 60.0, clock=clock, max_keys=3)
        now[0] = 0.0
        self.assertTrue(lim.allow("10.0.0.1"))
        now[0] = 1.0
        self.assertTrue(lim.allow("10.0.0.2"))
        now[0] = 2.0
        self.assertTrue(lim.allow("10.0.0.3"))
        now[0] = 100.0  # semua key lama sudah idle (window 60 dtk lewat)
        self.assertTrue(lim.allow("10.0.0.4"))
        self.assertLessEqual(len(lim._hits), 3)
        self.assertNotIn("10.0.0.1", lim._hits)  # aktivitas terlama -> dibuang
        self.assertIn("10.0.0.4", lim._hits)  # key baru tidak dikorbankan

    def test_limiter_still_enforces_per_key_budget_after_eviction(self):
        # Eviksi tidak merusak penegakan budget per key.
        now, clock = self._clock()
        lim = webchat._SlidingWindowLimiter(2, 60.0, clock=clock, max_keys=2)
        self.assertTrue(lim.allow("10.0.0.1"))
        self.assertTrue(lim.allow("10.0.0.1"))
        self.assertFalse(lim.allow("10.0.0.1"))  # budget habis
        self.assertTrue(lim.allow("10.0.0.2"))
        self.assertTrue(lim.allow("10.0.0.3"))  # eviksi key tertua
        self.assertLessEqual(len(lim._hits), 2)

    def test_post_flood_returns_429(self):
        sessions = _Sessions()
        tiny = webchat._SlidingWindowLimiter(2, 60.0)
        original = webchat._POST_LIMITER
        webchat._POST_LIMITER = tiny
        try:
            with _Server(sessions, _cfg()) as srv:
                body = {"chat_id": "banjir", "text": "halo"}
                s1, _, _ = srv.request("/api/message", method="POST", body=body)
                s2, _, _ = srv.request("/api/message", method="POST", body=body)
                s3, payload, _ = srv.request("/api/message", method="POST", body=body)
        finally:
            webchat._POST_LIMITER = original
        self.assertEqual((s1, s2, s3), (200, 200, 429))
        self.assertIn("rate limited", payload.decode("utf-8"))
        # Hanya 2 turn yang dijalankan agen.
        self.assertEqual(len(sessions.calls), 2)


class AuthTests(unittest.TestCase):
    def test_no_token_get_root_serves_login_only(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, body, ctype = srv.request("/", token=None)
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("text/html", ctype)
        self.assertIn('id="login"', text)
        self.assertIn("Masukkan token", text)
        # Halaman login tidak memuat panel chat / data sensitif.
        self.assertNotIn('id="composer"', text)
        self.assertNotIn(TOKEN, text)

    def test_wrong_token_get_root_is_401(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, _body, _ctype = srv.request("/", token="token-salah-yang-panjang")
        self.assertEqual(status, 401)

    def test_correct_token_get_root_serves_chat(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, body, ctype = srv.request("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("text/html", ctype)
        self.assertIn('id="composer"', text)
        self.assertIn('id="messages"', text)
        self.assertNotIn(TOKEN, text)

    def test_health_needs_no_auth_and_leaks_nothing(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, body, _ctype = srv.request("/health", token=None)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload, {"ok": True, "service": "zeline-webchat"})
        self.assertNotIn(TOKEN, body.decode("utf-8"))

    def test_message_endpoint_requires_auth(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, _body, _ctype = srv.request(
                "/api/message",
                method="POST",
                body={"chat_id": "a", "text": "halo"},
                token=None,
            )
            self.assertEqual(status, 401)
            status, _body, _ctype = srv.request(
                "/api/message",
                method="POST",
                body={"chat_id": "a", "text": "halo"},
                token="token-salah-yang-panjang",
            )
            self.assertEqual(status, 401)

    def test_status_endpoint_requires_auth(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, _body, _ctype = srv.request("/api/status", token=None)
            self.assertEqual(status, 401)
            status, _body, _ctype = srv.request("/api/status", token="token-salah-yang-panjang")
            self.assertEqual(status, 401)


class MessageTests(unittest.TestCase):
    def test_round_trip_uses_webchat_identity(self):
        sessions = _Sessions(reply="halo juga")
        with _Server(sessions, _cfg()) as srv:
            status, body, _ctype = srv.request(
                "/api/message",
                method="POST",
                body={"chat_id": "abc123", "text": "halo zeline"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["reply"], "halo juga")
        self.assertEqual(len(sessions.calls), 1)
        call = sessions.calls[0]
        self.assertEqual(call["identity"], "webchat:abc123")
        self.assertEqual(call["text"], "halo zeline")
        self.assertEqual(call["tool_profile"], "safe")

    def test_x_zeline_token_header_also_works(self):
        sessions = _Sessions(reply="ok")
        with _Server(sessions, _cfg()) as srv:
            req = urllib.request.Request(
                srv.url("/api/message"),
                data=json.dumps({"chat_id": "a", "text": "t"}).encode(),
                headers={"X-Zeline-Token": TOKEN, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)

    def test_empty_text_is_400(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, body, _ctype = srv.request(
                "/api/message", method="POST", body={"chat_id": "a", "text": "   "}
            )
        self.assertEqual(status, 400)
        self.assertIn("text is required", body.decode("utf-8"))

    def test_chat_id_too_long_is_400(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, _body, _ctype = srv.request(
                "/api/message", method="POST", body={"chat_id": "x" * 257, "text": "halo"}
            )
        self.assertEqual(status, 400)

    def test_oversize_body_is_413(self):
        sessions = _Sessions()
        with _Server(sessions, _cfg()) as srv:
            big = json.dumps({"chat_id": "a", "text": "x" * (webchat.MAX_BODY_BYTES + 1)})
            req = urllib.request.Request(
                srv.url("/api/message"),
                data=big.encode("utf-8"),
                headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
        self.assertEqual(status, 413)
        self.assertEqual(sessions.calls, [])

    def test_broken_json_is_400(self):
        with _Server(_Sessions(), _cfg()) as srv:
            req = urllib.request.Request(
                srv.url("/api/message"),
                data=b"{bukan json",
                headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
        self.assertEqual(status, 400)

    def test_non_object_json_is_400(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, _body, _ctype = srv.request("/api/message", method="POST", body=["array"])
        self.assertEqual(status, 400)

    def test_agent_error_maps_to_502(self):
        with _Server(_Sessions(exc=ZelineError("provider down")), _cfg()) as srv:
            status, body, _ctype = srv.request(
                "/api/message", method="POST", body={"chat_id": "a", "text": "t"}
            )
        self.assertEqual(status, 502)
        self.assertIn("provider down", body.decode("utf-8"))

    def test_unexpected_error_maps_to_500_without_leak(self):
        with _Server(_Sessions(exc=RuntimeError("rahasia-internal")), _cfg()) as srv:
            status, body, _ctype = srv.request(
                "/api/message", method="POST", body={"chat_id": "a", "text": "t"}
            )
        self.assertEqual(status, 500)
        self.assertNotIn("rahasia-internal", body.decode("utf-8"))


class StatusTests(unittest.TestCase):
    def test_status_has_promised_fields_and_no_secrets(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, body, _ctype = srv.request("/api/status")
        self.assertEqual(status, 200)
        raw = body.decode("utf-8")
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertIn("model", payload)
        self.assertIsInstance(payload["workers"], list)
        self.assertIsInstance(payload["usage_today"], dict)
        lowered = raw.lower()
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn("api_key", lowered)
        self.assertNotIn("api-keys", lowered)
        # Worker yang dilaporkan ringkas: hanya id/task/status.
        for worker in payload["workers"]:
            self.assertLessEqual(set(worker), {"id", "task", "status"})

    def test_status_model_field_exists(self):
        with _Server(_Sessions(), _cfg()) as srv:
            status, body, _ctype = srv.request("/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        # Model boleh None bila config tidak terbaca; kuncinya harus ada.
        self.assertIn("model", payload)


class XssTests(unittest.TestCase):
    def test_chat_html_never_uses_raw_innerhtml(self):
        # Akses properti .innerHTML (bukan sekadar kata di komentar).
        self.assertIsNone(re.search(r"\.innerHTML", webchat._CHAT_HTML))
        self.assertIsNone(re.search(r"\.innerHTML", webchat._LOGIN_HTML))

    def test_chat_js_renders_text_via_textcontent(self):
        self.assertIn("textContent", webchat._CHAT_HTML)

    def test_script_reply_round_trips_verbatim_as_json(self):
        """JSON tetap memuat teks mentah; escaping terjadi di sisi render
        (textContent), bukan dengan mengubah isi balasan."""
        payload = '<script>alert("xss")</script><img src=x onerror=alert(2)>'
        with _Server(_Sessions(reply=payload), _cfg()) as srv:
            status, body, _ctype = srv.request(
                "/api/message", method="POST", body={"chat_id": "a", "text": "t"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["reply"], payload)


class AskRendererTests(unittest.TestCase):
    """Renderer fail-fast untuk interaction.ask() di identitas webchat.

    Tanpa renderer terdaftar, ask() jatuh ke event.wait(180) — POST
    /api/message hang 3 menit sementara session lock tertahan dan chat_id
    terblokir total. Renderer ini harus menolak SEGERA (fail-closed).
    """

    def test_ask_without_renderer_takes_the_wait_path(self):
        """Mendokumentasikan mekanisme bug: tanpa renderer, ask() menunggu
        sampai timeout penuh (di sini dipendekkan dari 180 dtk)."""
        identity = "webchat:no-renderer-probe"
        interaction.unregister_channel(identity)
        with unittest.mock.patch.object(interaction, "_timeout_seconds", return_value=1.0):
            started = time.monotonic()
            result = interaction.ask(identity, "Approve run_shell?", ["Allow", "Deny"])
            elapsed = time.monotonic() - started
        self.assertTrue(result.startswith("NO ANSWER"), result)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertIsNone(interaction.pending(identity))

    def test_ask_denies_immediately_and_fail_closed(self):
        identity = "webchat:deny-probe"
        webchat._register_ask_renderer(identity)
        try:
            started = time.monotonic()
            result = interaction.ask(identity, "Approve run_shell?", ["Allow", "Deny"])
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 5.0, f"ask() hang {elapsed:.1f}s — renderer tidak fail-fast")
            # Fail-closed: parse_verdict memetakan string ini ke "deny".
            self.assertEqual(approvals.parse_verdict(result), "deny")
            self.assertIn("Deny", result)
            # Pesan mengarahkan ke kanal yang bisa approve, tanpa membocorkan
            # isi pertanyaan (bisa memuat perintah shell penuh).
            self.assertIn("Telegram", result)
            self.assertIn("CLI", result)
            self.assertNotIn("run_shell", result)
            # Tidak ada pertanyaan menggantung setelah renderer menjawab.
            self.assertIsNone(interaction.pending(identity))
        finally:
            webchat._unregister_ask_renderer(identity)

    def test_ask_guard_refcount_survives_overlapping_turns(self):
        """Dua turn berbarengan untuk chat_id yang sama (yang kedua antre di
        session lock): unregister turn pertama tidak boleh mencabut renderer
        selagi turn kedua masih berjalan."""
        identity = "webchat:overlap-probe"
        webchat._register_ask_renderer(identity)
        webchat._register_ask_renderer(identity)
        try:
            webchat._unregister_ask_renderer(identity)  # turn pertama selesai
            result = interaction.ask(identity, "Approve?", ["Allow", "Deny"])
            self.assertEqual(approvals.parse_verdict(result), "deny")
        finally:
            webchat._unregister_ask_renderer(identity)  # turn kedua selesai
        # Setelah semua turn pergi, channel bersih (tidak bocor).
        with unittest.mock.patch.object(interaction, "_timeout_seconds", return_value=1.0):
            result = interaction.ask(identity, "Approve?", ["Allow", "Deny"])
        self.assertTrue(result.startswith("NO ANSWER"), result)

    def test_worker_identity_resolves_to_parent_renderer(self):
        """Identitas worker webchat:<chat_id>::wkr<id> harus ter-resolve ke
        renderer yang didaftarkan untuk webchat:<chat_id> (prefix-aware) —
        worker webchat ikut mendapat renderer fail-fast, bukan hang."""
        identity = "webchat:wkr-resolve-probe"
        webchat._register_ask_renderer(identity)
        try:
            started = time.monotonic()
            result = interaction.ask(
                f"{identity}::wkr7f3a", "Approve run_shell?", ["Allow", "Deny"]
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 5.0, f"worker webchat hang {elapsed:.1f}s")
            self.assertEqual(approvals.parse_verdict(result), "deny")
            self.assertIn("Deny", result)
            self.assertIsNone(interaction.pending(f"{identity}::wkr7f3a"))
        finally:
            webchat._unregister_ask_renderer(identity)

    def test_exact_identity_wins_over_parent_prefix(self):
        """Renderer yang didaftarkan persis untuk identitas worker dipakai
        dulu — fallback ke parent hanya bila tidak ada exact match."""
        parent = "webchat:wkr-exact-probe"
        worker = f"{parent}::wkr9"
        webchat._register_ask_renderer(parent)
        interaction.register_channel(worker, lambda _entry: "worker-inline-answer")
        try:
            result = interaction.ask(worker, "Question?", ["Allow", "Deny"])
            self.assertEqual(result, "worker-inline-answer")
        finally:
            interaction.unregister_channel(worker)
            webchat._unregister_ask_renderer(parent)

    def test_non_webchat_worker_identity_unchanged(self):
        """Lookup prefix-aware hanya untuk pola webchat:X::wkr*: identitas
        worker gateway lain yang tak terdaftar tetap menempuh jalur wait
        (perilaku lama, di luar scope item ini)."""
        identity = "other:9::wkr7"
        interaction.unregister_channel(identity)
        with unittest.mock.patch.object(interaction, "_timeout_seconds", return_value=1.0):
            result = interaction.ask(identity, "Approve?", ["Allow", "Deny"])
        self.assertTrue(result.startswith("NO ANSWER"), result)

    def test_message_handler_denies_approval_without_hanging(self):
        """End-to-end: turn yang memicu ask() di dalam sessions.send harus
        selesai cepat dengan deny, bukan hang 180 dtk."""

        class _AskingSessions(_Sessions):
            def send(self, identity, text, tool_profile, **kwargs):
                verdict = interaction.ask(identity, "Approve rm -rf /tmp/x?", ["Allow", "Deny"])
                return f"verdict={approvals.parse_verdict(verdict)}"

        sessions = _AskingSessions()
        with _Server(sessions, _cfg()) as srv:
            started = time.monotonic()
            status, body, _ctype = srv.request(
                "/api/message", method="POST", body={"chat_id": "m2", "text": "hapus itu"}
            )
            elapsed = time.monotonic() - started
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 15.0, f"POST hang {elapsed:.1f}s")
        self.assertEqual(json.loads(body)["reply"], "verdict=deny")
        # Channel dibersihkan setelah turn (tidak bocor ke request berikut).
        with unittest.mock.patch.object(interaction, "_timeout_seconds", return_value=1.0):
            result = interaction.ask("webchat:m2", "Approve?", ["Allow", "Deny"])
        self.assertTrue(result.startswith("NO ANSWER"), result)


if __name__ == "__main__":
    unittest.main()
