"""Contract tests for ``send_file``: the agent must be able to DELIVER a file.

Zeline could already produce a PNG, an XLSX, or a PDF and had no way to hand it
to the operator — the model printed a filesystem path, which is unusable from a
phone. These tests pin the whole path: workspace sandboxing, the classification a
gateway needs, every failure mode, and the fact that a channel is registered only
for the duration of a turn.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class SendFileToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._old_env = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = str(self.home)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.delivery = importlib.import_module("zeline.delivery")
        self.tools = importlib.import_module("zeline.tools")
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.delivery.unregister_channel("test:session")
        if self._old_env is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old_env
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def _file(self, name: str, data: bytes = b"payload") -> Path:
        path = self.workspace / name
        path.write_bytes(data)
        return path

    def test_the_tool_is_registered_and_reaches_the_owner_profiles(self):
        definition = next(d for d in self.tools.TOOL_DEFS if d.name == "send_file")
        # Producing files needs the workspace, so a public `safe` gateway must not
        # be able to push arbitrary workspace files into a chat.
        self.assertEqual(sorted(definition.profiles), ["full", "workspace"])
        self.assertNotIn("safe", definition.profiles)
        self.assertEqual(definition.schema()["function"]["parameters"]["required"], ["path"])

    def test_a_registered_channel_receives_path_caption_and_kind(self):
        self._file("report.pdf")
        seen: dict[str, object] = {}

        def sender(path, caption, kind):
            seen.update(path=Path(path).name, caption=caption, kind=kind)
            return True

        self.delivery.register_channel("test:session", sender)
        result = self.tools._send_file("report.pdf", self.workspace, "test:session", "laporan")
        self.assertIn("Sent report.pdf", result)
        self.assertEqual(seen, {"path": "report.pdf", "caption": "laporan", "kind": "document"})

    def test_images_audio_and_video_are_classified_for_the_gateway(self):
        """The gateway picks sendPhoto/sendAudio from this, so it must be right."""
        cases = {
            "chart.png": "image",
            "photo.JPG": "image",
            "sticker.webp": "image",
            "note.ogg": "audio",
            "song.mp3": "audio",
            "clip.mp4": "video",
            "book.pdf": "document",
            "sheet.xlsx": "document",
            "archive.zip": "document",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.delivery.kind_for(Path(name)), expected)

    def test_without_a_channel_it_reports_the_path_instead_of_failing(self):
        """A headless run (CLI, cron) still produced the file.

        Returning an ERROR here would make the model retry a delivery that has no
        channel to deliver through, or conclude the file was never written.
        """
        self._file("out.csv")
        result = self.tools._send_file("out.csv", self.workspace, "nobody:here")
        self.assertIn("NOT DELIVERED", result)
        self.assertIn("out.csv", result)
        self.assertNotIn("ERROR", result)

    def test_path_escape_is_refused(self):
        self.delivery.register_channel("test:session", lambda *a: True)
        result = self.tools._send_file("../../etc/passwd", self.workspace, "test:session")
        self.assertIn("ERROR send_file", result)
        self.assertIn("inside the workspace", result)

    def test_missing_and_empty_files_are_refused_before_upload(self):
        calls = []
        self.delivery.register_channel("test:session", lambda *a: calls.append(a) or True)
        missing = self.tools._send_file("nope.txt", self.workspace, "test:session")
        self.assertIn("not a file or not found", missing)
        self._file("empty.csv", b"")
        empty = self.tools._send_file("empty.csv", self.workspace, "test:session")
        self.assertIn("is empty (0 bytes)", empty)
        # Neither reached the channel: an empty upload is rejected by the platform
        # with an opaque error, so it is caught here where the message is useful.
        self.assertEqual(calls, [])

    def test_oversized_file_is_refused_with_the_measured_size(self):
        big = self.workspace / "huge.bin"
        with big.open("wb") as handle:
            handle.truncate(self.delivery.MAX_DELIVERY_BYTES + 1)
        self.delivery.register_channel("test:session", lambda *a: True)
        result = self.tools._send_file("huge.bin", self.workspace, "test:session")
        self.assertIn("ERROR send_file", result)
        self.assertIn("MB", result)
        self.assertIn("limit", result)

    def test_a_broken_channel_never_kills_the_turn(self):
        self._file("report.pdf")

        def boom(path, caption, kind):
            raise RuntimeError("network down")

        self.delivery.register_channel("test:session", boom)
        result = self.tools._send_file("report.pdf", self.workspace, "test:session")
        self.assertIn("ERROR send_file", result)
        self.assertIn("RuntimeError", result)

    def test_a_platform_rejection_is_reported_as_a_rejection(self):
        self._file("report.pdf")
        self.delivery.register_channel("test:session", lambda *a: False)
        result = self.tools._send_file("report.pdf", self.workspace, "test:session")
        self.assertIn("rejected report.pdf", result)

    def test_the_caption_is_bounded(self):
        self._file("report.pdf")
        seen: dict[str, str] = {}
        self.delivery.register_channel(
            "test:session", lambda path, caption, kind: seen.update(caption=caption) or True
        )
        self.tools._send_file("report.pdf", self.workspace, "test:session", "x" * 5000)
        self.assertLessEqual(len(seen["caption"]), self.delivery.MAX_CAPTION_CHARS)

    def test_the_executor_wires_the_tool_to_its_own_identity(self):
        """Delivery must follow the session, or a file lands in the wrong chat."""
        self._file("report.pdf")
        received: list[str] = []
        self.delivery.register_channel(
            "telegram:4242", lambda path, caption, kind: received.append(Path(path).name) or True
        )
        executor = self.tools.ToolExecutor(
            "telegram:4242", profile="full", workspace=str(self.workspace)
        )
        out = executor.run("send_file", {"path": "report.pdf"})
        self.assertIn("Sent report.pdf", out)
        self.assertEqual(received, ["report.pdf"])
        self.delivery.unregister_channel("telegram:4242")

    def test_a_public_safe_gateway_cannot_call_it(self):
        executor = self.tools.ToolExecutor(
            "telegram:public", profile="safe", workspace=str(self.workspace)
        )
        denied = executor.run("send_file", {"path": "report.pdf"})
        self.assertIn("not allowed for profile", denied.lower())


class TelegramFileUploadTests(unittest.TestCase):
    """The gateway half: correct Bot API method per kind, with a real fallback."""

    def setUp(self) -> None:
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_each_kind_maps_to_its_bot_api_method(self):
        self.assertEqual(self.telegram._UPLOAD_METHODS["image"], ("sendPhoto", "photo"))
        self.assertEqual(self.telegram._UPLOAD_METHODS["audio"], ("sendAudio", "audio"))
        self.assertEqual(self.telegram._UPLOAD_METHODS["video"], ("sendVideo", "video"))
        self.assertEqual(self.telegram._UPLOAD_METHODS["document"], ("sendDocument", "document"))

    def _post(self, results):
        calls = []

        class Response:
            def __init__(self, ok, payload):
                self.ok = ok
                self._payload = payload

            def json(self):
                return self._payload

        def post(url, data=None, files=None, timeout=None):
            calls.append((url.rsplit("/", 1)[-1], sorted((files or {}).keys())))
            ok, payload = results.pop(0)
            return Response(ok, payload)

        return post, calls

    def test_an_image_is_sent_as_a_photo_not_a_document(self):
        tmp = Path(tempfile.mkdtemp()) / "chart.png"
        tmp.write_bytes(b"\x89PNG fake")
        post, calls = self._post([(True, {"ok": True})])
        with mock.patch.object(self.telegram._HTTP, "post", side_effect=post):
            sent = self.telegram._send_produced_file("api", 1, tmp, "grafik", "image")
        self.assertTrue(sent)
        self.assertEqual(calls, [("sendPhoto", ["photo"])])

    def test_a_rejected_photo_falls_back_to_a_document(self):
        """Telegram refuses some valid images (ratio, dimensions, size).

        Falling back means the operator still receives the file; failing outright
        would mean the work is done and the result never arrives.
        """
        tmp = Path(tempfile.mkdtemp()) / "wide.png"
        tmp.write_bytes(b"\x89PNG fake")
        post, calls = self._post([
            (False, {"ok": False, "description": "PHOTO_INVALID_DIMENSIONS"}),
            (True, {"ok": True}),
        ])
        with mock.patch.object(self.telegram._HTTP, "post", side_effect=post):
            sent = self.telegram._send_produced_file("api", 1, tmp, "", "image")
        self.assertTrue(sent)
        self.assertEqual(calls, [("sendPhoto", ["photo"]), ("sendDocument", ["document"])])

    def test_a_document_is_not_retried_twice(self):
        """The fallback IS sendDocument, so retrying it would double every failure."""
        tmp = Path(tempfile.mkdtemp()) / "book.pdf"
        tmp.write_bytes(b"%PDF fake")
        post, calls = self._post([(False, {"ok": False, "description": "FILE_TOO_BIG"})])
        with mock.patch.object(self.telegram._HTTP, "post", side_effect=post):
            sent = self.telegram._send_produced_file("api", 1, tmp, "", "document")
        self.assertFalse(sent)
        self.assertEqual(calls, [("sendDocument", ["document"])])

    def test_the_progress_feed_names_the_file_being_sent(self):
        line = self.telegram._tool_progress_text("send_file", {"path": "reports/q3.xlsx"})
        self.assertEqual(line, "📤 Sending file <code>q3.xlsx</code>")
        self.assertFalse(line.startswith("🔧"))


if __name__ == "__main__":
    unittest.main()
