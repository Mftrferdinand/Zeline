"""Tests for the media-tools batch: text_to_speech, qr_code, transcribe_audio, pdf_tool."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _FakeResp:
    def __init__(self, ok=True, status=200, content=b"", payload=None):
        self.ok = ok
        self.status_code = status
        self.content = content
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _make_pdf(path: Path, pages: int = 1):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(200, 200)
    with open(path, "wb") as fh:
        w.write(fh)


class MediaToolsBatchTest(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module("zeline.tools")
        self.config = importlib.import_module("zeline.config")
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self._orig_key = self.config.API_KEY
        self._orig_base = self.config.BASE_URL
        self.config.API_KEY = "x"
        self.config.BASE_URL = "https://api.openai.com/v1"

    def tearDown(self):
        self.config.API_KEY = self._orig_key
        self.config.BASE_URL = self._orig_base
        self._tmp.cleanup()

    # ---- text_to_speech ----
    def test_tts_happy_path(self):
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(content=b"A" * 4096)) as mp:
            out = self.tools._text_to_speech("halo dunia", "voice.mp3", self.ws)
        self.assertTrue(out.startswith("OK"), out)
        self.assertGreater((self.ws / "voice.mp3").stat().st_size, 1024)
        _, kwargs = mp.call_args
        self.assertIn("/audio/speech", mp.call_args[0][0])
        self.assertEqual(kwargs["json"]["voice"], "alloy")

    def test_tts_rejects_empty_and_too_long(self):
        self.assertIn("ERROR", self.tools._text_to_speech("", "v.mp3", self.ws))
        self.assertIn("ERROR", self.tools._text_to_speech("x" * 4001, "v.mp3", self.ws))

    def test_tts_rejects_non_mp3(self):
        self.assertIn("ERROR", self.tools._text_to_speech("hi", "v.wav", self.ws))

    def test_tts_404_hint(self):
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(ok=False, status=404)):
            out = self.tools._text_to_speech("hi", "v.mp3", self.ws)
        self.assertIn("ERROR", out)
        self.assertIn("/audio/speech", out)

    def test_tts_400_surfaces_provider_reason_not_zeline_bug(self):
        """Regresi: 400 dulu bilang 'This is a Zeline-side bug'.

        Nyata di 9Router: 'tts-1' diminta tapi provider OpenAI tidak punya
        kredensial → 400 'No credentials for provider: openai'. Pesan yang
        menyalahkan Zeline menyembunyikan penyebab sebenarnya.
        """
        payload = {"error": {"message": "No credentials for provider: openai"}}
        with mock.patch.object(
            self.tools.requests, "post", return_value=_FakeResp(ok=False, status=400, payload=payload)
        ):
            out = self.tools._text_to_speech("hi", "v.mp3", self.ws)
        self.assertIn("ERROR", out)
        self.assertIn("No credentials for provider: openai", out)
        self.assertNotIn("Zeline-side bug", out)
        self.assertIn("text-to-speech is not available", out)

    def test_tts_400_without_json_body_still_explains(self):
        """Provider 400 tanpa body JSON tetap dapat pesan actionable."""
        with mock.patch.object(self.tools.requests, "post", return_value=_FakeResp(ok=False, status=400)):
            out = self.tools._text_to_speech("hi", "v.mp3", self.ws)
        self.assertIn("ERROR", out)
        self.assertIn("text-to-speech is not available", out)
        self.assertNotIn("Zeline-side bug", out)

    # ---- qr_code ----
    def test_qr_happy_path(self):
        out = self.tools._qr_code("https://example.com", "qr.png", self.ws)
        self.assertTrue(out.startswith("OK"), out)
        data = (self.ws / "qr.png").read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_qr_rejects_empty_and_bad_ext(self):
        self.assertIn("ERROR", self.tools._qr_code("", "qr.png", self.ws))
        self.assertIn("ERROR", self.tools._qr_code("x", "qr.txt", self.ws))

    # ---- transcribe_audio ----
    def test_transcribe_happy_path(self):
        (self.ws / "note.ogg").write_bytes(b"fake-audio")
        stt = importlib.import_module("zeline.transcribe")
        with mock.patch.object(stt, "transcribe", return_value="halo ini tes") as mt:
            out = self.tools._transcribe_audio("note.ogg", self.ws, language="id")
        self.assertTrue(out.startswith("OK"), out)
        self.assertIn("halo ini tes", out)
        mt.assert_called_once()

    def test_transcribe_missing_file(self):
        out = self.tools._transcribe_audio("ghost.ogg", self.ws)
        self.assertIn("ERROR", out)

    def test_transcribe_surfaces_engine_error(self):
        (self.ws / "note.ogg").write_bytes(b"fake-audio")
        stt = importlib.import_module("zeline.transcribe")
        with mock.patch.object(stt, "transcribe", side_effect=stt.TranscribeError("no model")):
            out = self.tools._transcribe_audio("note.ogg", self.ws)
        self.assertIn("ERROR", out)
        self.assertIn("no model", out)

    # ---- pdf_tool ----
    def test_pdf_info(self):
        _make_pdf(self.ws / "a.pdf", 3)
        out = self.tools._pdf_tool("info", "", self.ws, pdfs="a.pdf")
        self.assertTrue(out.startswith("OK"), out)
        self.assertIn("3 page", out)

    def test_pdf_merge(self):
        _make_pdf(self.ws / "a.pdf", 2)
        _make_pdf(self.ws / "b.pdf", 1)
        out = self.tools._pdf_tool("merge", "merged.pdf", self.ws, pdfs="a.pdf,b.pdf")
        self.assertTrue(out.startswith("OK"), out)
        from pypdf import PdfReader

        self.assertEqual(len(PdfReader(str(self.ws / "merged.pdf")).pages), 3)

    def test_pdf_split(self):
        _make_pdf(self.ws / "a.pdf", 4)
        out = self.tools._pdf_tool("split", "part.pdf", self.ws, pdfs="a.pdf", pages="2-3")
        self.assertTrue(out.startswith("OK"), out)
        from pypdf import PdfReader

        self.assertEqual(len(PdfReader(str(self.ws / "part.pdf")).pages), 2)

    def test_pdf_split_bad_pages(self):
        _make_pdf(self.ws / "a.pdf", 2)
        out = self.tools._pdf_tool("split", "part.pdf", self.ws, pdfs="a.pdf", pages="9")
        self.assertIn("ERROR", out)

    def test_pdf_unknown_action_and_missing(self):
        self.assertIn("ERROR", self.tools._pdf_tool("compress", "x.pdf", self.ws, pdfs="a.pdf"))
        self.assertIn("ERROR", self.tools._pdf_tool("merge", "x.pdf", self.ws, pdfs="ghost.pdf"))

    def test_pdf_blocks_path_escape(self):
        out = self.tools._pdf_tool("info", "", self.ws, pdfs="../escape.pdf")
        self.assertIn("ERROR", out)

    # ---- registration & labels ----
    def test_registered_in_workspace_and_full_not_safe(self):
        ws_exec = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=self.ws)
        full_exec = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.ws)
        safe_exec = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.ws)
        names = {"text_to_speech", "qr_code", "transcribe_audio", "pdf_tool"}
        for ex in (ws_exec, full_exec):
            have = {item["function"]["name"] for item in ex.all_schemas}
            self.assertTrue(names <= have, names - have)
        have_safe = {item["function"]["name"] for item in safe_exec.all_schemas}
        self.assertTrue(names.isdisjoint(have_safe))

    def test_telegram_progress_labels(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertIn("🔊", telegram._tool_progress_text("text_to_speech", {}))
        self.assertIn("🔳", telegram._tool_progress_text("qr_code", {}))
        self.assertIn("🎙️", telegram._tool_progress_text("transcribe_audio", {}))
        label = telegram._tool_progress_text("pdf_tool", {"action": "merge"})
        self.assertIn("📄", label)
        self.assertIn("merge", label)


if __name__ == "__main__":
    unittest.main()
