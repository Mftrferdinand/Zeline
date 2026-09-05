"""Contract tests for inbound-audio transcription.

The gateway already downloaded voice notes to the media inbox. From there the trail
stopped: ``analyze_media`` replied *"transcribe first (e.g. an STT/Whisper tool)"* and
no such tool existed anywhere in Zeline — grep for whisper/stt/speech returned
nothing. So on the platform Zeline is used from, the most natural way to send a long
instruction produced an apology.

These tests use a local stand-in server rather than mocking ``requests``: the whole
chain (file on disk → multipart upload → JSON parse → text handed to the agent) is
what was broken, so that is what gets exercised.
"""
from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock


class _Recorder:
    """State shared with the stand-in server thread."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200
        self.payload: bytes = json.dumps({"text": "transcribed text"}).encode()


def _make_server(recorder: _Recorder) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            recorder.requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization", ""),
                    "content_type": self.headers.get("Content-Type", ""),
                    "body": body,
                }
            )
            self.send_response(recorder.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(recorder.payload)))
            self.end_headers()
            self.wfile.write(recorder.payload)

        def log_message(self, *args, **kwargs):
            pass

    return HTTPServer(("127.0.0.1", 0), Handler)


class TranscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="zl-stt-home-"))
        self.workspace = Path(tempfile.mkdtemp(prefix="zl-stt-ws-"))
        self._old_env = {
            key: os.environ.get(key)
            for key in ("ZELINE_HOME", "ZELINE_BASE_URL", "ZELINE_API_KEY", "ZELINE_AUDIO_MODEL")
        }
        self.recorder = _Recorder()
        self.server = _make_server(self.recorder)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        os.environ["ZELINE_HOME"] = str(self.home)
        os.environ["ZELINE_BASE_URL"] = f"http://127.0.0.1:{self.server.server_port}/v1"
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_AUDIO_MODEL"] = "whisper-1"
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.config = importlib.import_module("zeline.config")
        self.transcribe = importlib.import_module("zeline.transcribe")
        self.tools = importlib.import_module("zeline.tools")
        self.executor = self.tools.ToolExecutor(
            "telegram:4242", profile="workspace", workspace=str(self.workspace)
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.workspace, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def _reload(self) -> None:
        self.config._set_runtime_values(self.config.load_config())

    def _voice(self, name: str = "voice.ogg", size: int = 4000) -> Path:
        path = self.workspace / name
        path.write_bytes(b"OggS" + b"\x00" * size)
        return path

    def _wav(self, name: str = "tone.wav", seconds: float = 0.5) -> Path:
        path = self.workspace / name
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(
                b"".join(
                    struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 16000)))
                    for i in range(int(16000 * seconds))
                )
            )
        return path

    # -- the regression this exists for
    def test_analyze_media_transcribes_audio_instead_of_explaining_stt(self):
        self._voice()
        self.recorder.payload = json.dumps({"text": "ringkas commit hari ini"}).encode()
        result = self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertIn("Transcript of `voice.ogg`", result)
        self.assertIn("ringkas commit hari ini", result)
        # The old reply pointed at a tool that did not exist.
        self.assertNotIn("STT/Whisper", result)

    def test_the_request_matches_the_openai_transcription_route(self):
        self._voice()
        self.executor.run("analyze_media", {"path_or_url": "voice.ogg", "question": "Zeline"})
        self.assertEqual(len(self.recorder.requests), 1)
        sent = self.recorder.requests[0]
        self.assertEqual(sent["path"], "/v1/audio/transcriptions")
        self.assertEqual(sent["auth"], "Bearer test-key")
        self.assertTrue(sent["content_type"].startswith("multipart/form-data"))
        self.assertIn(b'name="file"', sent["body"])
        self.assertIn(b'name="model"', sent["body"])
        # `question` becomes the API's vocabulary hint, not an instruction.
        self.assertIn(b'name="prompt"', sent["body"])
        self.assertIn(b"Zeline", sent["body"])

    def test_a_question_is_optional(self):
        self._voice()
        self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertNotIn(b'name="prompt"', self.recorder.requests[0]["body"])

    # -- configuration
    def test_a_missing_audio_model_is_reported_not_guessed(self):
        """Guessing a model name produces a confusing 404 instead of an answer."""
        os.environ.pop("ZELINE_AUDIO_MODEL", None)
        self._reload()
        self._voice()
        result = self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertIn("no transcription model is set", result)
        self.assertIn("ZELINE_AUDIO_MODEL", result)
        self.assertEqual(self.recorder.requests, [])

    def test_audio_model_is_configurable_by_file_and_environment(self):
        self.assertEqual(self.config.AUDIO_MODEL, "whisper-1")
        self.assertIn("audio_model", self.config.stored_config_copy()["provider"])

    # -- format handling
    def test_a_native_format_is_uploaded_without_conversion(self):
        """Telegram sends Ogg/Opus, which the API accepts. Converting buys nothing."""
        path = self._voice()
        with mock.patch("subprocess.run", side_effect=AssertionError("must not convert")):
            self.transcribe.transcribe(path)
        self.assertEqual(len(self.recorder.requests), 1)

    def test_a_convertible_format_goes_through_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not installed")
        source = self._wav("source.wav")
        target = self.workspace / "note.amr"
        import subprocess

        converted = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-ar", "8000", "-ac", "1", str(target)],
            capture_output=True,
            check=False,
        )
        if converted.returncode != 0 or not target.is_file():
            self.skipTest("this ffmpeg build cannot write .amr")
        self.assertIn("transcribed text", self.transcribe.transcribe(target))

    def test_a_convertible_format_without_ffmpeg_says_what_to_do(self):
        path = self.workspace / "note.amr"
        path.write_bytes(b"#!AMR\n" + b"\x00" * 500)
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(self.transcribe.TranscribeError) as caught:
                self.transcribe.transcribe(path)
        self.assertIn("ffmpeg is not installed", str(caught.exception))
        self.assertIn("ogg/mp3/m4a/wav", str(caught.exception))

    def test_an_unsupported_extension_is_not_treated_as_audio(self):
        (self.workspace / "notes.xyz").write_bytes(b"data")
        result = self.executor.run("analyze_media", {"path_or_url": "notes.xyz"})
        self.assertIn("neither an image nor audio/video", result)

    def test_video_is_transcribed_but_labelled_audio_only(self):
        """Reporting a soundtrack as though the model had watched the picture is a lie."""
        (self.workspace / "clip.mp4").write_bytes(b"\x00" * 3000)
        result = self.executor.run("analyze_media", {"path_or_url": "clip.mp4"})
        self.assertIn("AUDIO TRACK ONLY", result)
        self.assertIn("extract key frames", result)

    def test_images_still_go_to_the_vision_model(self):
        """Audio handling must not swallow the path this tool already had."""
        (self.workspace / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        self.executor.run("analyze_media", {"path_or_url": "shot.png"})
        paths = [item["path"] for item in self.recorder.requests]
        self.assertNotIn("/v1/audio/transcriptions", paths)

    # -- refusals before upload
    def test_empty_and_missing_and_oversized_files_are_refused_locally(self):
        (self.workspace / "empty.ogg").write_bytes(b"")
        self.assertIn("is empty", self.executor.run("analyze_media", {"path_or_url": "empty.ogg"}))
        self.assertIn(
            "not a file", self.executor.run("analyze_media", {"path_or_url": "nope.ogg"})
        )
        big = self.workspace / "long.ogg"
        with big.open("wb") as handle:
            handle.truncate(self.transcribe.MAX_UPLOAD_BYTES + 1)
        oversized = self.executor.run("analyze_media", {"path_or_url": "long.ogg"})
        self.assertIn("MB", oversized)
        self.assertIn("limit", oversized)
        # None of these reached the provider: an opaque 413 is not actionable.
        self.assertEqual(self.recorder.requests, [])

    def test_escaping_the_workspace_is_refused(self):
        result = self.executor.run("analyze_media", {"path_or_url": "../../etc/passwd"})
        self.assertIn("ERROR", result)
        self.assertEqual(self.recorder.requests, [])

    # -- provider failures
    def test_a_provider_error_message_is_surfaced(self):
        self.recorder.status = 400
        self.recorder.payload = json.dumps(
            {"error": {"message": "No credentials for provider: openai"}}
        ).encode()
        self._voice()
        result = self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertIn("provider HTTP 400", result)
        self.assertIn("No credentials", result)

    def test_a_404_explains_that_the_route_or_model_is_missing(self):
        self.recorder.status = 404
        self.recorder.payload = b"{}"
        self._voice()
        result = self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertIn("/audio/transcriptions", result)
        self.assertIn("whisper-1", result)

    def test_an_empty_transcript_is_an_error_not_an_empty_answer(self):
        self.recorder.payload = json.dumps({"text": "   "}).encode()
        self._voice()
        result = self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertIn("no transcript text", result)

    def test_a_non_json_response_is_reported_plainly(self):
        self.recorder.payload = b"<html>gateway error</html>"
        self._voice()
        result = self.executor.run("analyze_media", {"path_or_url": "voice.ogg"})
        self.assertIn("non-JSON", result)


class VoicePromptTests(unittest.TestCase):
    """The gateway prompt has to tell the agent to ACT, not to narrate."""

    def setUp(self) -> None:
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_a_voice_note_is_treated_as_the_users_message(self):
        prompt = self.telegram._build_media_notice_prompt(
            "audio", Path("/inbox/voice.ogg"), "ringkas ini"
        )
        self.assertIn("analyze_media", prompt)
        self.assertIn("AS THE USER'S MESSAGE", prompt)
        self.assertIn("ringkas ini", prompt)
        # The old prompt promised the tool would "explain the correct handling".
        self.assertNotIn("explain the correct", prompt)

    def test_a_video_prompt_mentions_frames_as_well_as_the_transcript(self):
        prompt = self.telegram._build_media_notice_prompt("video", Path("/inbox/clip.mp4"))
        self.assertIn("audio track", prompt)
        self.assertIn("ffmpeg", prompt)

    def test_the_feed_says_transcribing_for_audio_and_looking_for_images(self):
        self.assertEqual(
            self.telegram._tool_progress_text("analyze_media", {"path_or_url": "/x/voice.ogg"}),
            "🎧 Transcribing audio…",
        )
        self.assertEqual(
            self.telegram._tool_progress_text("analyze_media", {"path_or_url": "/x/clip.mp4"}),
            "🎧 Transcribing audio…",
        )
        self.assertEqual(
            self.telegram._tool_progress_text("analyze_media", {"path_or_url": "/x/shot.png"}),
            "🖼 Looking at image…",
        )


if __name__ == "__main__":
    unittest.main()
