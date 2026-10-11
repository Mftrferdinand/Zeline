"""Local image generation: OpenAI DALL-E langsung dan Stable Diffusion lokal.

Semua backend bersifat opsional; di VM/CI tanpa backend terpasang,
yang diuji adalah jalur gagal-jujur (ImageGenError) dan logika deteksi.
Jalur sukses diuji dengan mock.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline import image_gen as ig_mod

# 1x1 PNG valid (base64) untuk mock respons API.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


class ValidationTests(unittest.TestCase):
    def test_empty_prompt(self):
        with self.assertRaises(ig_mod.ImageGenError):
            ig_mod.generate_image("", "/tmp/x.png")

    def test_prompt_too_long(self):
        with self.assertRaises(ig_mod.ImageGenError):
            ig_mod.generate_image("x" * (ig_mod.MAX_PROMPT_CHARS + 1), "/tmp/x.png")

    def test_bad_extension(self):
        with self.assertRaises(ig_mod.ImageGenError):
            ig_mod.generate_image("a cat", "/tmp/x.txt")

    def test_unknown_backend(self):
        with self.assertRaises(ig_mod.ImageGenError) as ctx:
            ig_mod.generate_image("a cat", "/tmp/x.png", backend="midjourney")
        self.assertIn("tidak dikenal", str(ctx.exception))

    def test_no_backend_honest(self):
        # Tanpa OPENAI_API_KEY, tanpa diffusers, tanpa web UI.
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.dict(sys.modules, {"diffusers": None, "torch": None}), \
             mock.patch.object(ig_mod, "_sd_webui_reachable", return_value=False):
            with self.assertRaises(ig_mod.ImageGenError) as ctx:
                ig_mod.generate_image("a cat", "/tmp/x.png")
            msg = str(ctx.exception)
            self.assertIn("OPENAI_API_KEY", msg)
            self.assertIn("diffusers", msg)


class OpenaiBackendTests(unittest.TestCase):
    def _mock_urlopen(self, payload: dict):
        body = json.dumps(payload).encode()

        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return body if n < 0 else body[:n]

        return mock.patch("urllib.request.urlopen", return_value=FakeResp())

    def test_openai_success(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "cat.png"
            payload = {"data": [{"b64_json": _PNG_B64}]}
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}), \
                 self._mock_urlopen(payload):
                result = ig_mod.generate_image("a cat", out, backend="openai")
            self.assertEqual(result, out)
            self.assertTrue(out.exists())
            self.assertEqual(out.read_bytes(), base64.b64decode(_PNG_B64))

    def test_openai_needs_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ig_mod.ImageGenError) as ctx:
                ig_mod.generate_image("a cat", "/tmp/x.png", backend="openai")
            self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_openai_bad_size(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with self.assertRaises(ig_mod.ImageGenError):
                ig_mod.generate_image("a cat", "/tmp/x.png", backend="openai",
                                      size="999x999")

    def test_openai_available_detection(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            self.assertTrue(ig_mod.openai_available())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ig_mod.openai_available())

    def test_openai_api_error(self):
        import urllib.error

        def _raise(*a, **k):
            raise urllib.error.HTTPError(
                "https://api.openai.com/v1/images/generations", 401, "Unauthorized",
                {}, io.BytesIO(b'{"error": {"message": "bad key"}}'),
            )

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-bad"}), \
             mock.patch("urllib.request.urlopen", side_effect=_raise):
            with self.assertRaises(ig_mod.ImageGenError) as ctx:
                ig_mod.generate_image("a cat", Path(td) / "x.png", backend="openai")
            self.assertIn("401", str(ctx.exception))


class SdBackendTests(unittest.TestCase):
    def test_sd_no_diffusers_no_webui(self):
        with mock.patch.dict(sys.modules, {"diffusers": None, "torch": None}), \
             mock.patch.object(ig_mod, "_sd_webui_reachable", return_value=False):
            with self.assertRaises(ig_mod.ImageGenError):
                ig_mod.generate_image("a cat", "/tmp/x.png", backend="sd")

    def test_sd_bad_size_format(self):
        with mock.patch.dict(sys.modules, {"diffusers": None}), \
             mock.patch.object(ig_mod, "_sd_webui_reachable", return_value=True):
            with self.assertRaises(ig_mod.ImageGenError):
                ig_mod._validate_size_sd("abc")

    def test_sd_size_bounds(self):
        with self.assertRaises(ig_mod.ImageGenError):
            ig_mod._validate_size_sd("4096x4096")
        self.assertEqual(ig_mod._validate_size_sd("512x512"), (512, 512))


class StatusTests(unittest.TestCase):
    def test_status_shape(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.dict(sys.modules, {"diffusers": None}), \
             mock.patch.object(ig_mod, "_sd_webui_reachable", return_value=False):
            st = ig_mod.image_gen_status()
        self.assertIn("openai", st)
        self.assertIn("sd", st)
        self.assertFalse(st["openai"]["available"])
        self.assertFalse(st["sd"]["available"])
        self.assertIn("OPENAI_API_KEY", st["openai"]["detail"])


class ToolWrapperTests(unittest.TestCase):
    def test_wrapper_no_backend_error_string(self):
        from zeline import tools as tools_mod
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.dict(sys.modules, {"diffusers": None, "torch": None}), \
                 mock.patch.object(ig_mod, "_sd_webui_reachable", return_value=False):
                out = tools_mod._image_generate_local("a cat", "cat.png", ws)
        self.assertTrue(out.startswith("ERROR:"))
        self.assertIn("OPENAI_API_KEY", out)

    def test_wrapper_path_traversal_blocked(self):
        from zeline import tools as tools_mod
        with tempfile.TemporaryDirectory() as td:
            out = tools_mod._image_generate_local("a cat", "../../evil.png", Path(td))
        self.assertTrue(out.startswith("ERROR:"))

    def test_wrapper_bad_extension(self):
        from zeline import tools as tools_mod
        with tempfile.TemporaryDirectory() as td:
            out = tools_mod._image_generate_local("a cat", "cat.txt", Path(td))
        self.assertTrue(out.startswith("ERROR:"))

if __name__ == "__main__":
    unittest.main()
