"""Tests for the generate_video tool (Veo text-to-video backend)."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _FakeResp:
    def __init__(self, ok=True, status=200, payload=None):
        self.ok = ok
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    # download-style context manager
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=65536):
        yield self._payload.get("_bytes", b"")


class GenerateVideoTest(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module("zeline.tools")
        self.config = importlib.import_module("zeline.config")
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self._orig_key = self.config.GEMINI_API_KEY
        self._orig_model = self.config.VIDEO_MODEL
        self.config.GEMINI_API_KEY = "test-gemini-key"
        self.config.VIDEO_MODEL = "veo-3.0-generate-001"

    def tearDown(self):
        self.config.GEMINI_API_KEY = self._orig_key
        self.config.VIDEO_MODEL = self._orig_model
        self._tmp.cleanup()

    def _run(self, **kwargs):
        return self.tools._generate_video(
            kwargs.get("prompt", "a city above the clouds"),
            kwargs.get("path", "clip.mp4"),
            self.ws,
            kwargs.get("duration", 8),
            kwargs.get("aspect_ratio", "16:9"),
            kwargs.get("operation", ""),
        )

    def test_requires_gemini_key(self):
        self.config.GEMINI_API_KEY = ""
        out = self._run()
        self.assertIn("ERROR", out)
        self.assertIn("Gemini API key", out)

    def test_requires_video_model(self):
        self.config.VIDEO_MODEL = ""
        out = self._run()
        self.assertIn("ERROR", out)
        self.assertIn("video model", out.lower())

    def test_rejects_non_mp4_path(self):
        out = self._run(path="clip.gif")
        self.assertIn("ERROR", out)
        self.assertIn(".mp4", out)

    def test_rejects_bad_duration(self):
        out = self._run(duration=30)
        self.assertIn("ERROR", out)

    def test_rejects_bad_aspect(self):
        out = self._run(aspect_ratio="4:3")
        self.assertIn("ERROR", out)

    def test_submit_404_suggests_model_check(self):
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(ok=False, status=404)):
            out = self._run()
        self.assertIn("ERROR", out)
        self.assertIn("veo-3.0-generate-001", out)

    def test_happy_path_polls_then_downloads(self):
        op = _FakeResp(payload={"name": "operations/abc123"})
        pending = _FakeResp(payload={"done": False})
        done = _FakeResp(payload={
            "done": True,
            "response": {"generatedSamples": [{"video": {"uri": "https://example.com/v.mp4"}}]},
        })
        dl = _FakeResp(payload={"_bytes": b"FAKEMP4" * 100})
        with mock.patch.object(self.tools.requests, "post", return_value=op), \
             mock.patch.object(self.tools.requests, "get", side_effect=[pending, done, dl]), \
             mock.patch.object(self.tools, "_VEO_POLL_INTERVAL", 0):
            out = self._run()
        self.assertTrue(out.startswith("OK"), out)
        dest = self.ws / "clip.mp4"
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), b"FAKEMP4" * 100)

    def test_timeout_returns_operation_for_resume(self):
        op = _FakeResp(payload={"name": "operations/slow9"})
        pending = _FakeResp(payload={"done": False})
        with mock.patch.object(self.tools.requests, "post", return_value=op), \
             mock.patch.object(self.tools.requests, "get", return_value=pending), \
             mock.patch.object(self.tools, "_VEO_POLL_INTERVAL", 0), \
             mock.patch.object(self.tools, "_VEO_POLL_MAX_ATTEMPTS", 3):
            out = self._run()
        self.assertIn("PENDING", out)
        self.assertIn("operations/slow9", out)

    def test_resume_existing_operation(self):
        done = _FakeResp(payload={
            "done": True,
            "response": {"generatedSamples": [{"video": {"uri": "https://example.com/v.mp4"}}]},
        })
        dl = _FakeResp(payload={"_bytes": b"RESUMED"})
        with mock.patch.object(self.tools.requests, "get", side_effect=[done, dl]) as mock_get, \
             mock.patch.object(self.tools.requests, "post") as mock_post:
            out = self._run(prompt="", operation="operations/slow9", path="resumed.mp4")
        mock_post.assert_not_called()
        self.assertTrue(out.startswith("OK"), out)
        self.assertEqual((self.ws / "resumed.mp4").read_bytes(), b"RESUMED")
        # poll hit the operation URL, not the submit endpoint
        self.assertIn("operations/slow9", mock_get.call_args_list[0][0][0])

    def test_job_error_surfaces_message(self):
        op = _FakeResp(payload={"name": "operations/bad"})
        failed = _FakeResp(payload={"done": True, "error": {"message": "quota exceeded"}})
        with mock.patch.object(self.tools.requests, "post", return_value=op), \
             mock.patch.object(self.tools.requests, "get", return_value=failed), \
             mock.patch.object(self.tools, "_VEO_POLL_INTERVAL", 0):
            out = self._run()
        self.assertIn("ERROR", out)
        self.assertIn("quota exceeded", out)

    def test_key_never_echoed_in_output(self):
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(ok=False, status=403)):
            out = self._run()
        self.assertNotIn("test-gemini-key", out)

    def test_registered_in_workspace_and_full_not_safe(self):
        ws_exec = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=self.ws)
        full_exec = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.ws)
        safe_exec = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.ws)
        for ex in (ws_exec, full_exec):
            self.assertIn("generate_video", {item["function"]["name"] for item in ex.all_schemas})
        self.assertNotIn("generate_video", {item["function"]["name"] for item in safe_exec.all_schemas})

    def test_telegram_progress_label(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        label = telegram._tool_progress_text("generate_video", {"prompt": "city above clouds"})
        self.assertIn("🎬", label)
        self.assertIn("city above clouds", label)


if __name__ == "__main__":
    unittest.main()
