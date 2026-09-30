"""Tests for the edit_image tool (provider /images/edits backend)."""
import base64
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


class EditImageTest(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module("zeline.tools")
        self.config = importlib.import_module("zeline.config")
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        (self.ws / "photo.png").write_bytes(b"\x89PNG-fake-bytes")
        self._orig_model = self.config.IMAGE_MODEL
        self._orig_key = self.config.API_KEY
        self._orig_base = self.config.BASE_URL
        self.config.IMAGE_MODEL = "gpt-image-1"
        self.config.API_KEY = "x"
        self.config.BASE_URL = "https://api.openai.com/v1"

    def tearDown(self):
        self.config.IMAGE_MODEL = self._orig_model
        self.config.API_KEY = self._orig_key
        self.config.BASE_URL = self._orig_base
        self._tmp.cleanup()

    def _run(self, **kwargs):
        return self.tools._edit_image(
            kwargs.get("image", "photo.png"),
            kwargs.get("prompt", "remove the people in the background"),
            kwargs.get("path", "edited.png"),
            self.ws,
            kwargs.get("mask", ""),
            kwargs.get("size", "1024x1024"),
        )

    def test_requires_image_model(self):
        self.config.IMAGE_MODEL = ""
        out = self._run()
        self.assertIn("ERROR", out)
        self.assertIn("image model", out.lower())

    def test_source_must_exist(self):
        out = self._run(image="missing.png")
        self.assertIn("ERROR", out)
        self.assertIn("not found", out)

    def test_source_must_be_image(self):
        (self.ws / "note.txt").write_text("hi")
        out = self._run(image="note.txt")
        self.assertIn("ERROR", out)

    def test_rejects_non_image_output(self):
        out = self._run(path="edited.txt")
        self.assertIn("ERROR", out)

    def test_rejects_bad_size(self):
        out = self._run(size="1x1")
        self.assertIn("ERROR", out)

    def test_mask_must_exist(self):
        out = self._run(mask="nomask.png")
        self.assertIn("ERROR", out)
        self.assertIn("mask", out.lower())

    def test_blocks_path_escape(self):
        out = self._run(image="../escape.png")
        self.assertIn("ERROR", out)
        self.assertFalse((self.ws.parent / "escape.png").exists())

    def test_happy_path_sends_multipart_and_saves(self):
        payload = {"data": [{"b64_json": base64.b64encode(b"EDITEDBYTES").decode()}]}
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(payload=payload)) as mp:
            out = self._run()
        self.assertTrue(out.startswith("OK"), out)
        self.assertIn("edited image", out)
        self.assertEqual((self.ws / "edited.png").read_bytes(), b"EDITEDBYTES")
        _, kwargs = mp.call_args
        self.assertIn("/images/edits", mp.call_args[0][0])
        self.assertIn("image", kwargs["files"])
        self.assertEqual(kwargs["data"]["model"], "gpt-image-1")
        self.assertNotIn("mask", kwargs["files"])

    def test_mask_is_sent_when_given(self):
        (self.ws / "mask.png").write_bytes(b"\x89PNG-mask")
        payload = {"data": [{"b64_json": base64.b64encode(b"X").decode()}]}
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(payload=payload)) as mp:
            out = self._run(mask="mask.png", path="masked.png")
        self.assertTrue(out.startswith("OK"), out)
        self.assertIn("mask", mp.call_args[1]["files"])

    def test_404_suggests_edits_support(self):
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(ok=False, status=404)):
            out = self._run()
        self.assertIn("ERROR", out)
        self.assertIn("edits", out)

    def test_url_response_downloads(self):
        dl = _FakeResp(payload={"_bytes": b"URLBYTES"})
        payload = {"data": [{"url": "https://cdn.example/e.png"}]}
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(payload=payload)), \
             mock.patch.object(self.tools.requests, "get", return_value=dl):
            out = self._run(path="from_url.png")
        self.assertTrue(out.startswith("OK"), out)
        self.assertEqual((self.ws / "from_url.png").read_bytes(), b"URLBYTES")

    def test_registered_in_workspace_and_full_not_safe(self):
        ws_exec = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=self.ws)
        full_exec = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.ws)
        safe_exec = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.ws)
        for ex in (ws_exec, full_exec):
            self.assertIn("edit_image", {item["function"]["name"] for item in ex.all_schemas})
        self.assertNotIn("edit_image", {item["function"]["name"] for item in safe_exec.all_schemas})

    def test_telegram_progress_label(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        label = telegram._tool_progress_text("edit_image", {"prompt": "remove people"})
        self.assertIn("✏️", label)
        self.assertIn("remove people", label)

    def test_generate_image_still_works_after_refactor(self):
        """Lock the _save_image_item refactor: generate_image happy path."""
        self.config.IMAGE_MODEL = "dall-e-3"
        payload = {"data": [{"b64_json": base64.b64encode(b"GENBYTES").decode()}]}
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(payload=payload)):
            out = self.tools._generate_image("a cat", "cat.png", self.ws)
        self.assertTrue(out.startswith("OK"), out)
        self.assertEqual((self.ws / "cat.png").read_bytes(), b"GENBYTES")


if __name__ == "__main__":
    unittest.main()
