"""Voice di core: TTS inti, preferensi per-chat, dan integrasi gateway.

Cakupan:
- ``zeline.voice``: synthesize happy path + semua fail-safe (VoiceError).
- ``zeline.voice_prefs``: mode/style per chat, file 0600, file corrupt aman.
- ``zeline.gateways.telegram``: VN masuk -> transcribe -> sessions.send;
  fallback alur lama; VN keluar per mode; /voice command.
"""
from __future__ import annotations

import gc
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import contextlib
import io
import queue
from pathlib import Path
from unittest import mock

from zeline import config
from zeline import voice as voice_mod
from zeline import voice_prefs


def _tmp_data_dir(testcase: unittest.TestCase) -> Path:
    """Arahkan config.DATA_DIR ke direktori sementara selama satu test."""
    tmp = Path(tempfile.mkdtemp(prefix="zl-voice-test-"))
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    patcher = mock.patch.object(config, "DATA_DIR", tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return tmp


class VoiceCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zl-tts-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _fake_run(self, *, opus_ok=True):
        def _run(cmd, **kwargs):
            if cmd[0] == "edge-tts":
                idx = cmd.index("--write-media")
                Path(cmd[idx + 1]).write_bytes(b"fake-mp3")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "ffmpeg":
                if opus_ok:
                    Path(cmd[-1]).write_bytes(b"fake-ogg")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, 1, "", "opus failed")
            raise AssertionError(f"unexpected command: {cmd[0]}")
        return _run

    def test_synthesize_happy_path_returns_ogg(self):
        with mock.patch("zeline.voice.shutil.which", return_value="/usr/bin/x"), \
             mock.patch("zeline.voice.subprocess.run", side_effect=self._fake_run()):
            result = voice_mod.synthesize("Halo, apa kabar?", out_dir=self.tmp)
        self.assertEqual(result.suffix, ".ogg")
        self.assertTrue(result.is_file())
        # mp3 sementara dibersihkan setelah konversi opus sukses
        self.assertFalse((self.tmp / "reply.mp3").exists())

    def test_synthesize_empty_text(self):
        with self.assertRaises(voice_mod.VoiceError):
            voice_mod.synthesize("   ")

    def test_synthesize_too_long(self):
        with self.assertRaisesRegex(voice_mod.VoiceError, "melebihi batas"):
            voice_mod.synthesize("x" * (voice_mod.MAX_TTS_CHARS + 1))

    def test_synthesize_unknown_style(self):
        with self.assertRaisesRegex(voice_mod.VoiceError, "tidak dikenal"):
            voice_mod.synthesize("halo", style="robot-jahat")

    def test_synthesize_missing_edge_tts(self):
        def which(name):
            return None if name == "edge-tts" else "/usr/bin/ffmpeg"
        with mock.patch("zeline.voice.shutil.which", side_effect=which):
            with self.assertRaisesRegex(voice_mod.VoiceError, "edge-tts"):
                voice_mod.synthesize("halo")

    def test_synthesize_missing_ffmpeg(self):
        def which(name):
            return None if name == "ffmpeg" else "/usr/bin/edge-tts"
        with mock.patch("zeline.voice.shutil.which", side_effect=which):
            with self.assertRaisesRegex(voice_mod.VoiceError, "ffmpeg"):
                voice_mod.synthesize("halo")

    def test_synthesize_edge_tts_failure(self):
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "network unreachable")
        with mock.patch("zeline.voice.shutil.which", return_value="/usr/bin/x"), \
             mock.patch("zeline.voice.subprocess.run", side_effect=_run):
            with self.assertRaisesRegex(voice_mod.VoiceError, "edge-tts gagal"):
                voice_mod.synthesize("halo", out_dir=self.tmp)

    def test_synthesize_opus_failure_falls_back_to_mp3(self):
        with mock.patch("zeline.voice.shutil.which", return_value="/usr/bin/x"), \
             mock.patch("zeline.voice.subprocess.run", side_effect=self._fake_run(opus_ok=False)):
            result = voice_mod.synthesize("halo", out_dir=self.tmp)
        self.assertEqual(result.suffix, ".mp3")
        self.assertTrue(result.is_file())

    def test_synthesize_rejects_argv_flag_injection(self):
        # V3: voice/rate/pitch yang diawali "-" akan diparse edge-tts
        # sebagai flag CLI (mis. "--proxy"), bukan data. Harus ditolak.
        for kwargs in ({"voice": "--proxy"},
                       {"rate": "--write-media"},
                       {"pitch": "--version"}):
            with self.assertRaises(voice_mod.VoiceError, msg=str(kwargs)):
                voice_mod.synthesize("halo", **kwargs)

    def test_synthesize_allows_plus_prefixed_presets(self):
        # Preset bawaan memakai "+10%"/"+35Hz" — awalan "+" bukan "-",
        # jadi tidak boleh kena guard injeksi flag.
        for preset_voice, preset_rate, preset_pitch in voice_mod.PRESETS.values():
            self.assertFalse(preset_voice.startswith("-"))
            self.assertFalse(preset_rate.startswith("-"))
            self.assertFalse(preset_pitch.startswith("-"))

    def test_synthesize_respects_style_override(self):
        seen = {}

        def _run(cmd, **kwargs):
            if cmd[0] == "edge-tts":
                seen["voice"] = cmd[cmd.index("--voice") + 1]
                idx = cmd.index("--write-media")
                Path(cmd[idx + 1]).write_bytes(b"x")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            Path(cmd[-1]).write_bytes(b"x")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch("zeline.voice.shutil.which", return_value="/usr/bin/x"), \
             mock.patch("zeline.voice.subprocess.run", side_effect=_run):
            voice_mod.synthesize("halo", style="gadis-anime", out_dir=self.tmp)
        self.assertEqual(seen["voice"], "id-ID-GadisNeural")

    def test_synthesize_no_out_dir_cleans_up_tempdir(self):
        # out_dir=None: direktori sementara membersihkan diri sendiri setelah
        # Path hasil tidak lagi direferensikan (tidak bocor di /tmp).
        with mock.patch("zeline.voice.shutil.which", return_value="/usr/bin/x"), \
             mock.patch("zeline.voice.subprocess.run", side_effect=self._fake_run()):
            result = voice_mod.synthesize("Halo, apa kabar?")
        self.assertEqual(result.suffix, ".ogg")
        self.assertTrue(result.is_file())
        tmpdir = result.parent
        self.assertTrue(tmpdir.is_dir())
        del result
        gc.collect()
        self.assertFalse(tmpdir.exists())

    def test_synthesize_no_out_dir_failure_leaves_no_tempdir(self):
        # Jalur gagal (VoiceError): tidak ada Path yang diserahkan, jadi
        # synthesize harus membersihkan temp dir-nya sendiri.
        def _fail(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "boom")

        def _leftovers():
            return {p.name for p in Path(tempfile.gettempdir()).glob("zl-tts-*")}

        before = _leftovers()
        with mock.patch("zeline.voice.shutil.which", return_value="/usr/bin/x"), \
             mock.patch("zeline.voice.subprocess.run", side_effect=_fail):
            with self.assertRaises(voice_mod.VoiceError):
                voice_mod.synthesize("halo")
        gc.collect()
        self.assertEqual(before, _leftovers())

    def test_styles_lists_all_presets(self):
        styles = voice_mod.styles()
        self.assertIn("emma-anime", styles)
        self.assertIn(voice_mod.DEFAULT_STYLE, styles)
        self.assertEqual(len(styles), len(voice_mod.PRESETS))


class VoicePrefsTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = _tmp_data_dir(self)
        self.identity = "telegram:4242"

    def test_defaults(self):
        prefs = voice_prefs.get_prefs(self.identity)
        self.assertEqual(prefs["voice_mode"], "text")
        self.assertEqual(prefs["voice_style"], "emma-anime")

    def test_set_mode_roundtrip(self):
        voice_prefs.set_mode(self.identity, "mirror")
        self.assertEqual(voice_prefs.voice_mode(self.identity), "mirror")

    def test_set_mode_invalid(self):
        with self.assertRaises(ValueError):
            voice_prefs.set_mode(self.identity, "keras")

    def test_set_style_roundtrip(self):
        voice_prefs.set_style(self.identity, "gadis-anime")
        self.assertEqual(voice_prefs.voice_style(self.identity), "gadis-anime")

    def test_set_style_invalid(self):
        with self.assertRaises(ValueError):
            voice_prefs.set_style(self.identity, "robot-jahat")

    def test_prefs_file_is_0600(self):
        voice_prefs.set_mode(self.identity, "always")
        path = voice_prefs._path(self.identity)
        self.assertTrue(path.is_file())
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600, f"expected 0600, got {oct(mode)}")

    def test_prefs_filename_hides_chat_id(self):
        voice_prefs.set_mode(self.identity, "mirror")
        name = voice_prefs._path(self.identity).name
        self.assertNotIn("4242", name)

    def test_corrupt_file_returns_defaults(self):
        voice_prefs.set_mode(self.identity, "mirror")
        voice_prefs._path(self.identity).write_text("{bukan json", encoding="utf-8")
        self.assertEqual(voice_prefs.get_prefs(self.identity)["voice_mode"], "text")

    def test_unknown_values_in_file_ignored(self):
        voice_prefs._path(self.identity).parent.mkdir(parents=True, exist_ok=True)
        voice_prefs._path(self.identity).write_text(
            json.dumps({"voice_mode": "bogus", "voice_style": "nope"}), encoding="utf-8"
        )
        prefs = voice_prefs.get_prefs(self.identity)
        self.assertEqual(prefs["voice_mode"], "text")
        self.assertEqual(prefs["voice_style"], "emma-anime")

    def test_wants_voice_reply_matrix(self):
        self.assertFalse(voice_prefs.wants_voice_reply(self.identity, came_from_voice=True))
        self.assertFalse(voice_prefs.wants_voice_reply(self.identity, came_from_voice=False))
        voice_prefs.set_mode(self.identity, "mirror")
        self.assertTrue(voice_prefs.wants_voice_reply(self.identity, came_from_voice=True))
        self.assertFalse(voice_prefs.wants_voice_reply(self.identity, came_from_voice=False))
        voice_prefs.set_mode(self.identity, "always")
        self.assertTrue(voice_prefs.wants_voice_reply(self.identity, came_from_voice=True))
        self.assertTrue(voice_prefs.wants_voice_reply(self.identity, came_from_voice=False))


class FakeSessions:
    """sessions.send() minimal untuk test jalur balasan gateway."""

    def __init__(self, reply_text: str):
        self.reply_text = reply_text
        self.sent_texts: list[str] = []

    def send(self, identity: str, text: str, **kwargs) -> str:
        self.sent_texts.append(text)
        return self.reply_text

    def reflect(self, identity: str):  # noqa: D102 - dipanggil di thread background
        return None


class TelegramVoiceTest(unittest.TestCase):
    def setUp(self):
        # Isolasi: test_cli / test_gateway_service mem-pop zeline.* dari
        # sys.modules di tengah suite. Tanpa import ulang yang konsisten,
        # objek `config` yang di-patch test bisa beda dengan yang dibaca
        # voice_prefs milik telegram → tulis ke ~/.zeline asli + assertion
        # gagal. Paksa satu set objek modul untuk test ini.
        for name in [n for n in list(sys.modules)
                     if n == "zeline" or n.startswith("zeline.")]:
            del sys.modules[name]
        import zeline.gateways.telegram as tg
        from zeline import config as _fresh_config
        from zeline import voice_prefs as _fresh_prefs
        global config, voice_prefs
        config, voice_prefs = _fresh_config, _fresh_prefs

        self.tg = tg
        self.data_dir = _tmp_data_dir(self)
        self.api = "https://api.telegram.org/botTESTTOKEN"
        self.identity = "telegram:111"

    def _voice_update(self) -> dict:
        return {
            "message": {
                "message_id": 5,
                "chat": {"id": 111},
                "from": {"id": 111},
                "voice": {"file_id": "voicefile123"},
            }
        }

    def _dispatch(self, update: dict, **patches):
        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_download_media_file",
                               return_value=(Path("/tmp/vn.ogg"), None)), \
             mock.patch.object(self.tg, "_transcribe_inbound_voice",
                               return_value=patches.get("transcript")), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            self.tg._dispatch_update(
                self.api, "TESTTOKEN", None, update,
                allowed=[111], tool_profile="safe", stop_event=stop_event,
            )
            # Transkripsi + dispatch kini berjalan di worker thread serial per
            # chat (bukan sinkron di loop polling): tunggu sampai
            # _start_agent_reply dipanggil.
            deadline = time.monotonic() + 5.0
            while not start.called and time.monotonic() < deadline:
                time.sleep(0.01)
        return start

    def test_voice_in_transcribed_to_sessions_send(self):
        start = self._dispatch(self._voice_update(), transcript="halo, ini pesan suara")
        start.assert_called_once()
        kwargs = start.call_args.kwargs
        self.assertIn("halo, ini pesan suara", kwargs["text"])
        self.assertTrue(kwargs["came_from_voice"])

    def test_voice_in_transcribe_failure_falls_back(self):
        # transcribe gagal / tak terkonfigurasi -> alur lama, tanpa exception
        start = self._dispatch(self._voice_update(), transcript=None)
        start.assert_called_once()
        kwargs = start.call_args.kwargs
        self.assertIn("analyze_media", kwargs["text"])
        self.assertTrue(kwargs["came_from_voice"])

    def test_audio_file_not_treated_as_voice_note(self):
        # File audio biasa (mp3 lagu) tetap ditranskrip, tapi TIDAK memicu
        # mirror mode — came_from_voice hanya untuk voice note asli Telegram.
        update = {
            "message": {
                "message_id": 6,
                "chat": {"id": 111},
                "from": {"id": 111},
                "audio": {"file_id": "audiofile456"},
            }
        }
        start = self._dispatch(update, transcript="lirik lagu")
        start.assert_called_once()
        kwargs = start.call_args.kwargs
        self.assertIn("lirik lagu", kwargs["text"])
        self.assertFalse(kwargs["came_from_voice"])

    def _maybe(self, reply, *, mode=None, came_from_voice=False, synth=None, send_voice=True):
        """Panggil _maybe_send_voice_reply dengan prefs & network di-mock."""
        if mode is not None:
            voice_prefs.set_mode(self.identity, mode)
        fake_path = Path(tempfile.mkdtemp(prefix="zl-tts-mock-")) / "reply.ogg"
        fake_path.write_bytes(b"ogg")
        self.addCleanup(lambda: __import__("shutil").rmtree(fake_path.parent, ignore_errors=True))
        if synth is None:
            synth = lambda text, **kw: fake_path  # noqa: E731
        with mock.patch.object(self.tg, "_api_call", return_value=None), \
             mock.patch.object(self.tg._voice_mod, "synthesize", side_effect=synth), \
             mock.patch.object(self.tg, "_send_voice", return_value=send_voice) as send:
            result = self.tg._maybe_send_voice_reply(
                self.api, 111, self.identity, reply, came_from_voice=came_from_voice
            )
        return result, send

    def test_outbound_default_text_mode_no_voice(self):
        (sent, note), send = self._maybe("Halo!", mode="text", came_from_voice=True)
        self.assertFalse(sent)
        self.assertEqual(note, "")
        send.assert_not_called()

    def test_outbound_mirror_voice_in_sends_voice(self):
        (sent, note), send = self._maybe("Halo, ini balasan suara!", mode="mirror",
                                         came_from_voice=True)
        self.assertTrue(sent)
        self.assertEqual(note, "")
        send.assert_called_once()

    def test_outbound_mirror_text_in_stays_text(self):
        (sent, note), send = self._maybe("Halo!", mode="mirror", came_from_voice=False)
        self.assertFalse(sent)
        self.assertEqual(note, "")
        send.assert_not_called()

    def test_outbound_always_voices_text_input(self):
        (sent, _), send = self._maybe("Halo!", mode="always", came_from_voice=False)
        self.assertTrue(sent)
        send.assert_called_once()

    def test_outbound_tts_failure_falls_back_to_text_with_note(self):
        def _boom(text, **kw):
            raise voice_mod.VoiceError("edge-tts belum terpasang")
        (sent, note), send = self._maybe("Halo!", mode="always", came_from_voice=False,
                                         synth=_boom)
        self.assertFalse(sent)
        self.assertIn("gagal", note)
        self.assertIn("teks", note)
        send.assert_not_called()

    def test_outbound_send_failure_falls_back_to_text_with_note(self):
        (sent, note), send = self._maybe("Halo!", mode="always", came_from_voice=False,
                                         send_voice=False)
        self.assertFalse(sent)
        self.assertIn("gagal terkirim", note)

    def test_outbound_long_reply_stays_text(self):
        long_reply = "x" * (voice_mod.MAX_TTS_CHARS + 1)
        (sent, note), send = self._maybe(long_reply, mode="always", came_from_voice=False)
        self.assertFalse(sent)
        self.assertEqual(note, "")
        send.assert_not_called()

    def test_outbound_code_block_stays_text(self):
        reply = "ini kodenya:\n```python\nprint('hi')\n```"
        (sent, _), send = self._maybe(reply, mode="always", came_from_voice=False)
        self.assertFalse(sent)
        send.assert_not_called()

    def test_send_voice_prefers_send_voice_method(self):
        calls = []

        class Resp:
            ok = True

            def json(self):
                return {"ok": True}

        def fake_post(url, **kwargs):
            calls.append(url)
            return Resp()

        with mock.patch.object(self.tg._HTTP, "post", side_effect=fake_post):
            path = Path(tempfile.mkdtemp(prefix="zl-sv-")) / "r.ogg"
            path.write_bytes(b"ogg")
            self.addCleanup(lambda: __import__("shutil").rmtree(path.parent, ignore_errors=True))
            self.assertTrue(self.tg._send_voice(self.api, 111, path))
        self.assertTrue(calls[0].endswith("/sendVoice"))

    def test_send_voice_falls_back_to_send_audio(self):
        calls = []

        class Resp:
            def __init__(self, ok):
                self.ok = ok

            def json(self):
                return {"ok": self.ok, "description": "rejected"}

        def fake_post(url, **kwargs):
            calls.append(url)
            return Resp(ok=url.endswith("/sendAudio"))

        with mock.patch.object(self.tg._HTTP, "post", side_effect=fake_post):
            path = Path(tempfile.mkdtemp(prefix="zl-sv-")) / "r.ogg"
            path.write_bytes(b"ogg")
            self.addCleanup(lambda: __import__("shutil").rmtree(path.parent, ignore_errors=True))
            self.assertTrue(self.tg._send_voice(self.api, 111, path))
        self.assertTrue(calls[0].endswith("/sendVoice"))
        self.assertTrue(calls[1].endswith("/sendAudio"))

    def test_full_reply_path_voice_skips_text(self):
        """_send_agent_reply: mode mirror + VN masuk -> VN terkirim, teks dilewati."""
        voice_prefs.set_mode(self.identity, "mirror")
        sessions = FakeSessions("Halo, ini jawaban suara!")
        fake_path = Path(tempfile.mkdtemp(prefix="zl-tts-mock-")) / "reply.ogg"
        fake_path.write_bytes(b"ogg")
        self.addCleanup(lambda: __import__("shutil").rmtree(fake_path.parent, ignore_errors=True))
        sent_messages = []

        def fake_api_call(api, method, **kwargs):
            if method == "sendMessage":
                sent_messages.append(kwargs.get("text"))
            return None

        with mock.patch.object(self.tg, "_api_call", side_effect=fake_api_call), \
             mock.patch.object(self.tg._voice_mod, "synthesize", return_value=fake_path), \
             mock.patch.object(self.tg, "_send_voice", return_value=True) as send_voice:
            self.tg._send_agent_reply(
                self.api, sessions, chat_id=111, identity=self.identity,
                text="prompt", tool_profile="safe", came_from_voice=True,
            )
        send_voice.assert_called_once()
        self.assertEqual(sent_messages, [], "teks tidak boleh terkirim saat VN terkirim")

    def test_full_reply_path_text_mode_sends_text(self):
        sessions = FakeSessions("Halo, jawaban teks biasa.")
        sent_messages = []

        def fake_api_call(api, method, **kwargs):
            if method == "sendMessage":
                sent_messages.append(kwargs.get("text"))
            return None

        with mock.patch.object(self.tg, "_api_call", side_effect=fake_api_call):
            self.tg._send_agent_reply(
                self.api, sessions, chat_id=111, identity=self.identity,
                text="prompt", tool_profile="safe", came_from_voice=True,
            )
        self.assertTrue(sent_messages, "mode text default harus mengirim teks")

    def test_voice_command(self):
        # tanpa arg: tampilkan status
        reply = self.tg._voice_command_reply(self.identity, "")
        self.assertIn("text", reply)
        # set mirror
        reply = self.tg._voice_command_reply(self.identity, "mirror")
        self.assertIn("mirror", reply)
        self.assertEqual(voice_prefs.voice_mode(self.identity), "mirror")
        # mode tak dikenal
        reply = self.tg._voice_command_reply(self.identity, "keras")
        self.assertIn("tidak dikenal", reply)
        # ganti style
        reply = self.tg._voice_command_reply(self.identity, "style gadis-anime")
        self.assertIn("gadis-anime", reply)
        self.assertEqual(voice_prefs.voice_style(self.identity), "gadis-anime")
        # style tak dikenal
        reply = self.tg._voice_command_reply(self.identity, "style robot")
        self.assertIn("tidak dikenal", reply)
        # daftar preset
        reply = self.tg._voice_command_reply(self.identity, "voices")
        self.assertIn("emma-anime", reply)


class TranscribeInboundTest(unittest.TestCase):
    def setUp(self):
        import zeline.gateways.telegram as tg

        self.tg = tg
        self.path = Path(tempfile.mkdtemp(prefix="zl-vn-")) / "note.ogg"
        self.path.write_bytes(b"ogg")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.path.parent, ignore_errors=True))

    def test_unconfigured_returns_none(self):
        with mock.patch.object(self.tg, "_transcribe_configured", return_value=False):
            self.assertIsNone(self.tg._transcribe_inbound_voice(self.path))

    def test_transcribe_error_returns_none(self):
        from zeline.transcribe import TranscribeError

        with mock.patch.object(self.tg, "_transcribe_configured", return_value=True), \
             mock.patch.object(self.tg, "_transcribe_audio",
                               side_effect=TranscribeError("model 404")):
            self.assertIsNone(self.tg._transcribe_inbound_voice(self.path))

    def test_success_returns_stripped_text(self):
        with mock.patch.object(self.tg, "_transcribe_configured", return_value=True), \
             mock.patch.object(self.tg, "_transcribe_audio", return_value="  halo dunia  "):
            self.assertEqual(self.tg._transcribe_inbound_voice(self.path), "halo dunia")

    def test_empty_transcript_returns_none(self):
        with mock.patch.object(self.tg, "_transcribe_configured", return_value=True), \
             mock.patch.object(self.tg, "_transcribe_audio", return_value="   "):
            self.assertIsNone(self.tg._transcribe_inbound_voice(self.path))


class VoiceWorkerTest(unittest.TestCase):
    """Transkripsi VN di worker thread serial per chat (bukan loop polling)."""

    def setUp(self):
        for name in [n for n in list(sys.modules)
                     if n == "zeline" or n.startswith("zeline.")]:
            del sys.modules[name]
        import zeline.gateways.telegram as tg
        from zeline import config as _fresh_config
        from zeline import voice_prefs as _fresh_prefs
        global config, voice_prefs
        config, voice_prefs = _fresh_config, _fresh_prefs
        self.tg = tg
        self.data_dir = _tmp_data_dir(self)
        self.api = "https://api.telegram.org/botTESTTOKEN"

    def _voice_update(self, chat_id: int, message_id: int = 5) -> dict:
        return {
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id},
                "from": {"id": chat_id},
                "voice": {"file_id": f"voicefile{message_id}"},
            }
        }

    def _wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate(), "timeout menunggu worker voice")

    def _dispatch_voice(self, chat_id: int, message_id: int, stop_event) -> None:
        self.tg._dispatch_update(
            self.api, "TESTTOKEN", None, self._voice_update(chat_id, message_id),
            allowed=[chat_id], tool_profile="safe", stop_event=stop_event,
        )

    def test_dispatch_does_not_block_polling(self):
        # Transkripsi 3 detik tidak boleh menahan loop polling >2 detik.
        chat_id = 501

        def slow_transcribe(path):
            time.sleep(3)
            return "lambat"

        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_api_call"), \
             mock.patch.object(self.tg, "_download_media_file",
                               return_value=(Path("/tmp/vn.ogg"), None)), \
             mock.patch.object(self.tg, "_transcribe_inbound_voice",
                               side_effect=slow_transcribe), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            t0 = time.monotonic()
            self._dispatch_voice(chat_id, 11, stop_event)
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0, "loop polling terblokir transkripsi")
            self._wait_for(lambda: start.called, timeout=8.0)
        self.assertIn("lambat", start.call_args.kwargs["text"])

    def test_voice_notes_processed_in_fifo_order(self):
        # VN pendek tidak boleh menyalip VN panjang dalam satu chat.
        chat_id = 502
        entered = threading.Event()
        release = threading.Event()
        calls: list[int] = []

        def fake_transcribe(path):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(timeout=10)
                return "pertama"
            return "kedua"

        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_api_call"), \
             mock.patch.object(self.tg, "_download_media_file",
                               return_value=(Path("/tmp/vn.ogg"), None)), \
             mock.patch.object(self.tg, "_transcribe_inbound_voice",
                               side_effect=fake_transcribe), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            self._dispatch_voice(chat_id, 11, stop_event)
            self._dispatch_voice(chat_id, 12, stop_event)
            self.assertTrue(entered.wait(timeout=5), "job pertama tidak mulai")
            release.set()
            self._wait_for(lambda: start.call_count == 2, timeout=5.0)
        texts = [c.kwargs["text"] for c in start.call_args_list]
        self.assertIn("pertama", texts[0])
        self.assertIn("kedua", texts[1])

    def test_worker_survives_job_error(self):
        # Error pada satu item tidak boleh mematikan worker chat itu.
        chat_id = 503
        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_api_call"), \
             mock.patch.object(self.tg, "_download_media_file",
                               return_value=(Path("/tmp/vn.ogg"), None)), \
             mock.patch.object(self.tg, "_transcribe_inbound_voice",
                               side_effect=["satu", "dua"]), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            start.side_effect = [RuntimeError("boom"), None]
            self._dispatch_voice(chat_id, 21, stop_event)
            self._dispatch_voice(chat_id, 22, stop_event)
            self._wait_for(lambda: start.call_count == 2, timeout=5.0)
        texts = [c.kwargs["text"] for c in start.call_args_list]
        self.assertIn("satu", texts[0])
        self.assertIn("dua", texts[1])

    def test_voice_queue_bound_refuses_flood(self):
        # Rate limit sederhana: antrean per chat dibatasi _VOICE_MAX_PENDING;
        # selebihnya ditolak dengan pesan yang jelas (bukan diam / bocor).
        chat_id = 504
        entered = threading.Event()
        release = threading.Event()

        def blocking_transcribe(path):
            entered.set()
            release.wait(timeout=10)
            return "x"

        stop_event = threading.Event()
        max_pending = self.tg._VOICE_MAX_PENDING
        with mock.patch.object(self.tg, "_api_call") as api_call, \
             mock.patch.object(self.tg, "_download_media_file",
                               return_value=(Path("/tmp/vn.ogg"), None)), \
             mock.patch.object(self.tg, "_transcribe_inbound_voice",
                               side_effect=blocking_transcribe), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            self._dispatch_voice(chat_id, 31, stop_event)
            self.assertTrue(entered.wait(timeout=5), "worker tidak mulai")
            for mid in range(32, 32 + max_pending):
                self._dispatch_voice(chat_id, mid, stop_event)
            # Antrean kini penuh → dispatch ini harus ditolak.
            self._dispatch_voice(chat_id, 99, stop_event)
            sent = [
                c.kwargs.get("text", "")
                for c in api_call.call_args_list
                if len(c.args) > 1 and c.args[1] == "sendMessage"
            ]
            self.assertTrue(
                any("Masih mentranskrip" in text for text in sent),
                "banjir VN tidak ditolak dengan pesan yang jelas",
            )
            release.set()
            self._wait_for(lambda: start.call_count == 1 + max_pending, timeout=10.0)

    def test_voice_command_owner_gated(self):
        # /voice mengubah mode balasan suara → permukaan operator, wajib owner.
        stop_event = threading.Event()
        # Bukan owner → ditolak dengan pesan yang jelas.
        with mock.patch.object(self.tg, "_api_call") as api_call:
            handled = self.tg._handle_command_update(
                self.api, "/voice", None, "telegram:222", 222,
                stop_event=stop_event, tool_profile="safe", allowed=[111],
            )
        self.assertTrue(handled)
        self.assertIn("Only the bot owner can use /voice.", api_call.call_args.kwargs["text"])
        # Owner → balasan normal.
        with mock.patch.object(self.tg, "_api_call") as api_call:
            handled = self.tg._handle_command_update(
                self.api, "/voice", None, "telegram:111", 111,
                stop_event=stop_event, tool_profile="safe", allowed=[111],
            )
        self.assertTrue(handled)
        self.assertIn("Voice reply", api_call.call_args.kwargs["text"])
        # Tanpa allowlist sama sekali → ditolak juga.
        with mock.patch.object(self.tg, "_api_call") as api_call:
            handled = self.tg._handle_command_update(
                self.api, "/voice", None, "telegram:333", 333,
                stop_event=stop_event, tool_profile="safe", allowed=[],
            )
        self.assertTrue(handled)
        self.assertIn("owner-only", api_call.call_args.kwargs["text"])

    def test_unknown_command_returns_none_not_forwarded(self):
        # Command tak dikenal SENGAJA ditelan lokal (None) — bukan diteruskan
        # ke model. Ini disengaja, bukan bug; didokumentasikan di docstring
        # _handle_command dan help /start.
        self.assertIsNone(
            self.tg._handle_command("/tak-dikenal", None, "telegram:111", stop_event=threading.Event())
        )

    def test_start_help_documents_unknown_commands_stay_local(self):
        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_api_call") as api_call:
            handled = self.tg._handle_command_update(
                self.api, "/start", None, "telegram:111", 111,
                stop_event=stop_event, tool_profile="safe", allowed=[111],
            )
        self.assertTrue(handled)
        self.assertIn("never sent to the model", api_call.call_args.kwargs["text"])


class VoiceWorkerHardeningTest(unittest.TestCase):
    """Regresi untuk temuan verifikator: worker tak bisa mati, self-healing,
    idle-cleanup, dan unduhan di worker (bukan loop polling)."""

    def setUp(self):
        for name in [n for n in list(sys.modules)
                     if n == "zeline" or n.startswith("zeline.")]:
            del sys.modules[name]
        import zeline.gateways.telegram as tg
        from zeline import config as _fresh_config
        from zeline import voice_prefs as _fresh_prefs
        global config, voice_prefs
        config, voice_prefs = _fresh_config, _fresh_prefs
        self.tg = tg
        self.data_dir = _tmp_data_dir(self)
        self.api = "https://api.telegram.org/botTESTTOKEN"

    def _voice_update(self, chat_id: int, message_id: int = 5) -> dict:
        return {
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id},
                "from": {"id": chat_id},
                "voice": {"file_id": f"voicefile{message_id}"},
            }
        }

    def _dispatch_voice(self, chat_id: int, message_id: int, stop_event) -> None:
        self.tg._dispatch_update(
            self.api, "TESTTOKEN", None, self._voice_update(chat_id, message_id),
            allowed=[chat_id], tool_profile="safe", stop_event=stop_event,
        )

    def _sent_texts(self, api_call) -> list[str]:
        return [
            c.kwargs.get("text", "")
            for c in api_call.call_args_list
            if len(c.args) > 1 and c.args[1] == "sendMessage"
        ]

    def _dead_thread_entry(self, chat_id: int, pending_jobs=()) -> None:
        """Sisipkan entry dengan worker yang SUDAH mati (simulasi kematian)."""
        dead = threading.Thread(target=lambda: None, daemon=True)
        dead.start()
        dead.join(timeout=5)
        self.assertFalse(dead.is_alive())
        pending = queue.Queue()
        for job in pending_jobs:
            pending.put(job)
        with self.tg._VOICE_WORKERS_LOCK:
            self.tg._VOICE_WORKERS[chat_id] = (pending, dead)

    # -- MAJOR-1a: SystemExit dari job tidak boleh membunuh worker -----------
    def test_worker_survives_systemexit_in_job(self):
        chat_id = 601
        done = threading.Event()
        results: list[str] = []

        def killer():
            raise SystemExit(1)

        def ok():
            results.append("ok")
            done.set()

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, killer), self.tg._VOICE_ENQUEUED)
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, ok), self.tg._VOICE_ENQUEUED)
            self.assertTrue(done.wait(timeout=5), "job setelah SystemExit tidak diproses")
        self.assertEqual(results, ["ok"])
        with self.tg._VOICE_WORKERS_LOCK:
            _pending, worker = self.tg._VOICE_WORKERS[chat_id]
        self.assertTrue(worker.is_alive(), "worker mati oleh SystemExit — pipeline lumpuh")

    # -- MAJOR-1b: exception di dalam error-handler print tidak membunuh loop -
    def test_worker_survives_failing_error_reporter(self):
        chat_id = 602
        done = threading.Event()

        def bad_job():
            raise ValueError("job rusak")

        def ok():
            done.set()

        def boom_print(*args, **kwargs):
            raise BrokenPipeError("stdout gone")

        with mock.patch.object(self.tg, "print", create=True, side_effect=boom_print):
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, bad_job), self.tg._VOICE_ENQUEUED)
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, ok), self.tg._VOICE_ENQUEUED)
            self.assertTrue(done.wait(timeout=5), "worker mati saat error-handler-nya gagal")
        with self.tg._VOICE_WORKERS_LOCK:
            _pending, worker = self.tg._VOICE_WORKERS[chat_id]
        self.assertTrue(worker.is_alive())

    # -- MAJOR-1: self-healing — worker mati -> restart, item lama diselamatkan
    def test_dead_worker_self_heals_and_rescues_pending(self):
        chat_id = 603
        order: list[str] = []
        self._dead_thread_entry(chat_id, pending_jobs=[lambda: order.append("rescued")])
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.tg._enqueue_voice_job(chat_id, lambda: order.append("new"))
        self.assertEqual(result, self.tg._VOICE_ENQUEUED)
        deadline = time.monotonic() + 5.0
        while len(order) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(order, ["rescued", "new"], "FIFO rusak / item lama hilang saat restart")
        with self.tg._VOICE_WORKERS_LOCK:
            _pending, worker = self.tg._VOICE_WORKERS[chat_id]
        self.assertTrue(worker.is_alive(), "worker baru tidak hidup setelah self-heal")

    # -- MAJOR-1: restart gagal -> tolak JUJUR, bukan "masih mentranskrip" ----
    def test_worker_restart_failure_rejects_honestly(self):
        chat_id = 604
        self._dead_thread_entry(chat_id)
        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_api_call") as api_call, \
             mock.patch.object(self.tg, "_spawn_voice_worker_locked",
                               side_effect=RuntimeError("no threads")), \
             mock.patch.object(self.tg, "_download_media_file") as dl:
            with contextlib.redirect_stdout(io.StringIO()):
                self._dispatch_voice(chat_id, 91, stop_event)
            sent = self._sent_texts(api_call)
        self.assertTrue(any("bermasalah" in text for text in sent),
                        f"penolakan tidak jujur: {sent}")
        self.assertFalse(any("Masih mentranskrip" in text for text in sent),
                         "pesan menyesatkan: janji transkripsi padahal worker mati")
        dl.assert_not_called()

    # -- MINOR-1: worker idle > timeout keluar sendiri + registry bersih ------
    def test_idle_worker_exits_and_registry_cleaned(self):
        chat_id = 605
        done = threading.Event()
        with mock.patch.object(self.tg, "_VOICE_WORKER_IDLE_SECS", 0.2):
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, done.set), self.tg._VOICE_ENQUEUED)
            self.assertTrue(done.wait(timeout=5))
            with self.tg._VOICE_WORKERS_LOCK:
                _pending, worker = self.tg._VOICE_WORKERS[chat_id]
            deadline = time.monotonic() + 5.0
            while worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(worker.is_alive(), "worker idle tidak keluar sendiri")
            with self.tg._VOICE_WORKERS_LOCK:
                self.assertNotIn(chat_id, self.tg._VOICE_WORKERS,
                                 "entry worker idle tidak dibersihkan")
            # Enqueue berikutnya membuat worker baru; pesan tetap diproses.
            done2 = threading.Event()
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, done2.set), self.tg._VOICE_ENQUEUED)
            self.assertTrue(done2.wait(timeout=5), "worker baru tidak memproses job")

    # -- MINOR-2: unduhan TIDAK di loop polling ------------------------------
    def test_download_happens_in_worker_not_polling_loop(self):
        chat_id = 606

        def slow_download(api, token, file_id, suffix):
            time.sleep(3)
            return (Path("/tmp/vn.ogg"), None)

        stop_event = threading.Event()
        with mock.patch.object(self.tg, "_api_call"), \
             mock.patch.object(self.tg, "_download_media_file", side_effect=slow_download), \
             mock.patch.object(self.tg, "_transcribe_inbound_voice", return_value="lambat"), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            t0 = time.monotonic()
            self._dispatch_voice(chat_id, 81, stop_event)
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0, "loop polling terblokir unduhan media")
            deadline = time.monotonic() + 8.0
            while not start.called and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(start.called, "worker tidak menyelesaikan unduh+transkripsi")
        self.assertIn("lambat", start.call_args.kwargs["text"])

    # -- MINOR-3: VN yang ditolak TIDAK diunduh -------------------------------
    def test_rejected_voice_note_never_downloaded(self):
        chat_id = 607
        entered = threading.Event()
        release = threading.Event()

        def blocking_transcribe(path):
            entered.set()
            release.wait(timeout=10)
            return "x"

        downloaded: list[str] = []

        def fake_download(api, token, file_id, suffix):
            downloaded.append(file_id)
            return (Path("/tmp/vn.ogg"), None)

        stop_event = threading.Event()
        max_pending = self.tg._VOICE_MAX_PENDING
        try:
            with mock.patch.object(self.tg, "_api_call") as api_call, \
                 mock.patch.object(self.tg, "_download_media_file", side_effect=fake_download), \
                 mock.patch.object(self.tg, "_transcribe_inbound_voice",
                                   side_effect=blocking_transcribe), \
                 mock.patch.object(self.tg, "_start_agent_reply"):
                self._dispatch_voice(chat_id, 71, stop_event)
                self.assertTrue(entered.wait(timeout=5), "worker tidak mulai")
                for mid in range(72, 72 + max_pending):
                    self._dispatch_voice(chat_id, mid, stop_event)
                # Antrean penuh -> dispatch ini ditolak SEBELUM unduh.
                self._dispatch_voice(chat_id, 99, stop_event)
                self.assertNotIn("voicefile99", downloaded,
                                 "VN yang ditolak ikut diunduh — bandwidth terbuang")
                sent = self._sent_texts(api_call)
                self.assertTrue(any("Masih mentranskrip" in text for text in sent),
                                "banjir VN tidak ditolak dengan pesan yang jelas")
        finally:
            release.set()

    # -- MINOR-2/3: gagal unduh -> pesan jelas, tanpa file yatim -------------
    def test_download_error_sends_clear_message_and_no_orphan(self):
        chat_id = 608
        stop_event = threading.Event()
        inbox = self.data_dir / "media-inbox"
        with mock.patch.object(self.tg, "MEDIA_INBOX", inbox), \
             mock.patch.object(self.tg, "_api_call") as api_call, \
             mock.patch.object(self.tg, "_download_media_file",
                               return_value=(None, "boom-fail")), \
             mock.patch.object(self.tg, "_start_agent_reply") as start:
            self._dispatch_voice(chat_id, 82, stop_event)
            deadline = time.monotonic() + 5.0
            sent: list[str] = []
            while time.monotonic() < deadline:
                sent = self._sent_texts(api_call)
                if any("tidak bisa diunduh" in text for text in sent):
                    break
                time.sleep(0.05)
            self.assertTrue(
                any("tidak bisa diunduh" in text and "boom-fail" in text for text in sent),
                f"pesan unduh-gagal tidak jelas: {sent}",
            )
            start.assert_not_called()
        leftovers = list(inbox.glob("voicefile82*")) if inbox.exists() else []
        self.assertEqual(leftovers, [], "file yatim di media-inbox")

    def test_partial_write_cleaned_no_orphan(self):
        inbox = self.data_dir / "media-inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / "voicefile90.ogg"
        dest.write_bytes(b"stale")
        resp = mock.Mock()
        resp.ok = True
        resp.content = b"0123456789abcdef"
        with mock.patch.object(self.tg, "MEDIA_INBOX", inbox), \
             mock.patch.object(self.tg, "_api_call",
                               return_value={"result": {"file_path": "voice/abc.ogg"}}), \
             mock.patch.object(self.tg.requests, "get", return_value=resp), \
             mock.patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            got_dest, error = self.tg._download_media_file(
                "https://api.telegram.org/botX", "TOK", "voicefile90", ".ogg")
        self.assertIsNone(got_dest)
        self.assertIn("Could not save", error or "")
        self.assertFalse(dest.exists(), "file setengah-tulis tidak dibersihkan")

    # -- pre-check murah: hanya butuh chat_id --------------------------------
    def test_voice_queue_full_precheck(self):
        chat_id = 609
        self.assertFalse(self.tg._voice_queue_full(chat_id), "belum ada worker -> tidak penuh")
        entered = threading.Event()
        release = threading.Event()

        def slow_job():
            entered.set()
            release.wait(timeout=10)

        try:
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, slow_job), self.tg._VOICE_ENQUEUED)
            self.assertTrue(entered.wait(timeout=5))
            self.assertFalse(self.tg._voice_queue_full(chat_id), "antrean kosong -> tidak penuh")
            for _ in range(self.tg._VOICE_MAX_PENDING):
                self.assertEqual(self.tg._enqueue_voice_job(chat_id, lambda: None),
                                 self.tg._VOICE_ENQUEUED)
            self.assertTrue(self.tg._voice_queue_full(chat_id), "antrean penuh tidak terdeteksi")
            self.assertEqual(self.tg._enqueue_voice_job(chat_id, lambda: None),
                             self.tg._VOICE_QUEUE_FULL)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
