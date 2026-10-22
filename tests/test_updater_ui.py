"""Updater presentation tests: never run installers or gateway lifecycle."""
import contextlib
import hashlib
import io
import re
from pathlib import Path
import unittest
from unittest.mock import patch
from zeline import updater

class Terminal(io.StringIO):
    def __init__(self, encoding="utf-8"):
        super().__init__()
        self._encoding = encoding
    @property
    def encoding(self):
        return self._encoding
    def isatty(self):
        return True

class UpdaterUITests(unittest.TestCase):
    def preview(self, *, windows=False, bad_checksum=False, failure=None, width=80, encoding="utf-8", installer_code=0):
        installer = "install.ps1" if windows else "install.sh"
        payload = b"preview installer: not executed"
        digest = "0" * 64 if bad_checksum else hashlib.sha256(payload).hexdigest()
        def fetch(url, **kwargs):
            return (digest + "  " + installer).encode() if url.endswith("SHA256SUMS") else payload
        stream = Terminal(encoding)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"TERM": "xterm-256color", "COLUMNS": str(width)}))
            stack.enter_context(patch.object(updater, "_is_windows", return_value=windows))
            stack.enter_context(patch.object(updater, "_pause_gateway_for_update", return_value=None))
            resume = stack.enter_context(patch.object(updater, "_resume_gateway_after_update"))
            stack.enter_context(patch.object(updater, "_checkout_root", return_value=None))
            stack.enter_context(patch.object(updater, "_latest_tag", return_value="v9.9.9", side_effect=failure))
            stack.enter_context(patch.object(updater, "_https_get", side_effect=fetch))
            run = stack.enter_context(patch.object(updater, "_run_installer", return_value=installer_code))
            stack.enter_context(contextlib.redirect_stdout(stream))
            result = updater.update()
        resume.assert_not_called()
        return result, stream.getvalue(), run

    def test_release_preview_has_distinct_stages_and_result(self):
        for windows in (False, True):
            code, output, run = self.preview(windows=windows)
            self.assertEqual(code, 0)
            for title in ("Update plan", "Release lookup", "Download", "Verification", "Installation", "Update complete"):
                self.assertIn(title, output)
            self.assertNotIn("████", output)
            run.assert_called_once()

    def test_failed_verification_never_shows_success(self):
        code, output, run = self.preview(bad_checksum=True)
        self.assertEqual(code, 1)
        self.assertIn("Update blocked", output)
        self.assertNotIn("Update complete", output)
        run.assert_not_called()

    def test_failed_lookup_has_error_result_without_install(self):
        code, output, run = self.preview(failure=RuntimeError("offline"))
        self.assertEqual(code, 1)
        self.assertIn("Update failed", output)
        self.assertNotIn("Update complete", output)
        run.assert_not_called()

    def test_phone_desktop_and_legacy_encoding_layout(self):
        from wcwidth import wcswidth
        for width, encoding in ((48, "utf-8"), (80, "utf-8"), (120, "utf-8"), (80, "cp1252")):
            code, output, _ = self.preview(width=width, encoding=encoding, windows=encoding == "cp1252")
            self.assertEqual(code, 0)
            plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
            plain.encode(encoding)
            for line in plain.splitlines():
                self.assertLessEqual(wcswidth(line), width)

    def test_local_source_has_its_own_stages_and_result(self):
        root = Path("/preview-only/source")
        stream = Terminal()
        with patch.dict("os.environ", {"TERM": "xterm-256color"}), \
             patch.object(updater, "_pause_gateway_for_update", return_value=None), \
             patch.object(updater, "_resume_gateway_after_update") as resume, \
             patch.object(updater, "_checkout_root", return_value=root), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(updater, "_run_installer", return_value=0) as run, \
             patch.object(updater, "_latest_tag") as latest, \
             contextlib.redirect_stdout(stream):
            code = updater.update()
        self.assertEqual(code, 0)
        latest.assert_not_called()
        resume.assert_not_called()
        run.assert_called_once_with(root / updater._installer_name(), root)
        self.assertIn("1 / 2", stream.getvalue())
        self.assertIn("Update complete", stream.getvalue())
        self.assertNotIn("Release lookup", stream.getvalue())

    def test_installer_failure_preserves_exit_code_and_has_no_success_card(self):
        code, output, run = self.preview(installer_code=23)
        self.assertEqual(code, 23)
        run.assert_called_once()
        self.assertIn("Update failed", output)
        self.assertNotIn("Update complete", output)
