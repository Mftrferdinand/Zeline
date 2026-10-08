"""Offline voice: faster-whisper STT dan piper/espeak TTS lokal.

Semua backend bersifat opsional; di VM/CI tanpa backend terpasang,
yang diuji adalah jalur gagal-jujur (VoiceError) dan logika deteksi.
Jalur sukses diuji dengan mock/fake binary.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from zeline import voice as voice_mod


def _make_wav(path: Path, seconds: float = 0.1) -> Path:
    import wave
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00" * int(8000 * seconds) * 2)
    return path


class SttDetectionTests(unittest.TestCase):
    def test_no_backend_honest(self):
        # None di sys.modules membuat `import faster_whisper` raise ImportError
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            self.assertEqual(voice_mod.stt_backends(), [])
            st = voice_mod.stt_status()
            self.assertFalse(st["available"])
            self.assertIn("faster-whisper", st["detail"])


class TranscribeValidationTests(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(voice_mod.VoiceError):
            voice_mod.transcribe("/tmp/tidak-ada-xyz-123.wav")

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            p = f.name
        try:
            with self.assertRaisesRegex(voice_mod.VoiceError, "kosong"):
                voice_mod.transcribe(p)
        finally:
            os.unlink(p)

    def test_oversize_file(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.truncate(voice_mod.MAX_AUDIO_BYTES + 1)
            p = f.name
        try:
            with self.assertRaisesRegex(voice_mod.VoiceError, "melebihi batas"):
                voice_mod.transcribe(p)
        finally:
            os.unlink(p)

    def test_bad_model_name(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"1234")
            p = f.name
        try:
            with self.assertRaisesRegex(voice_mod.VoiceError, "tidak dikenal"):
                voice_mod.transcribe(p, model="../../etc")
        finally:
            os.unlink(p)

    def test_no_faster_whisper(self):
        p = _make_wav(Path(tempfile.mkdtemp()) / "in.wav")
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaisesRegex(voice_mod.VoiceError, "faster-whisper"):
                voice_mod.transcribe(str(p))

    def test_model_not_cached_no_autodownload(self):
        """local_files_only=True harus dipakai — tidak boleh unduh diam-diam."""
        p = _make_wav(Path(tempfile.mkdtemp()) / "in.wav")
        fw = types.ModuleType("faster_whisper")
        seen = {}

        class FakeModel:
            def __init__(self, *a, **k):
                seen.update(k)
                raise RuntimeError("no cached model")

        fw.WhisperModel = FakeModel
        with mock.patch.dict(sys.modules, {"faster_whisper": fw}):
            with self.assertRaisesRegex(voice_mod.VoiceError, "download-model"):
                voice_mod.transcribe(str(p))
        self.assertTrue(seen.get("local_files_only"), "harus local_files_only=True")

    def test_transcribe_success_mocked(self):
        p = _make_wav(Path(tempfile.mkdtemp()) / "in.wav")
        fw = types.ModuleType("faster_whisper")

        class Seg:
            def __init__(self, t):
                self.text = t

        class FakeModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, path, language=None):
                return ([Seg(" halo"), Seg("dunia ")], {"language": language})

        fw.WhisperModel = FakeModel
        with mock.patch.dict(sys.modules, {"faster_whisper": fw}):
            self.assertEqual(voice_mod.transcribe(str(p), language="id"), "halo dunia")


class SpeakValidationTests(unittest.TestCase):
    def test_empty_text(self):
        with self.assertRaises(voice_mod.VoiceError):
            voice_mod.speak("   ", "/tmp/out.wav")

    def test_too_long(self):
        with self.assertRaisesRegex(voice_mod.VoiceError, "melebihi batas"):
            voice_mod.speak("x" * (voice_mod.MAX_SPEAK_CHARS + 1), "/tmp/out.wav")

    def test_bad_extension(self):
        with self.assertRaisesRegex(voice_mod.VoiceError, ".wav"):
            voice_mod.speak("halo", "/tmp/out.mp3")

    def test_no_backend(self):
        with mock.patch.object(voice_mod, "local_tts_backends", return_value=[]):
            with self.assertRaises(voice_mod.VoiceError):
                voice_mod.speak("halo", "/tmp/out.wav")

    def test_bad_backend_name(self):
        with self.assertRaisesRegex(voice_mod.VoiceError, "tidak dikenal"):
            voice_mod.speak("halo", "/tmp/out.wav", backend="festival")

    def test_unavailable_forced_backend(self):
        with mock.patch.object(voice_mod, "local_tts_backends", return_value=["espeak-ng"]):
            with self.assertRaisesRegex(voice_mod.VoiceError, "tidak tersedia"):
                voice_mod.speak("halo", "/tmp/out.wav", backend="piper")

    def test_speak_espeak_success_fake_binary(self):
        """Fake espeak-ng menulis WAV minimal; teks lewat stdin (list-form, no shell)."""
        bindir = Path(tempfile.mkdtemp())
        fake = bindir / "espeak-ng"
        fake.write_text(
            "#!/bin/bash\n"
            'OUT=""; PREV=""; for a in "$@"; do if [ "$PREV" = "-w" ]; then OUT="$a"; fi; PREV="$a"; done\n'
            "cat > /dev/null\n"
            'python3 -c "import struct,sys; open(sys.argv[1],\'wb\').write(struct.pack(\'<4sI4s\', b\'RIFF\', 36, b\'WAVE\'))" "$OUT"\n'
        )
        fake.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}):
            # shutil.which memakai PATH saat dipanggil
            out = Path(tempfile.mkdtemp()) / "out.wav"
            result = voice_mod.speak("halo; rm -rf /", str(out), voice="id")
            self.assertTrue(result.is_file())
            self.assertGreater(result.stat().st_size, 0)

    def test_piper_voice_symlink_rejected(self):
        d = Path(tempfile.mkdtemp())
        link = d / "evil.onnx"
        link.symlink_to("/etc/passwd")
        with mock.patch.dict(os.environ, {"PIPER_VOICE": str(link)}):
            self.assertIsNone(voice_mod._find_piper_voice())

    def test_no_mkdir_when_backend_missing(self):
        # V1: speak() tidak boleh membuat direktori output di jalur gagal —
        # mkdir hanya terjadi setelah backend dipastikan tersedia.
        d = Path(tempfile.mkdtemp()) / "must-not-exist" / "sub"
        with mock.patch.object(voice_mod, "local_tts_backends", return_value=[]):
            with self.assertRaises(voice_mod.VoiceError):
                voice_mod.speak("halo", str(d / "out.wav"))
        self.assertFalse(d.exists(),
                         "output dir must not be created when no backend is available")

    def test_no_mkdir_when_forced_backend_unavailable(self):
        d = Path(tempfile.mkdtemp()) / "must-not-exist-2" / "sub"
        with mock.patch.object(voice_mod, "local_tts_backends", return_value=["espeak-ng"]):
            with self.assertRaises(voice_mod.VoiceError):
                voice_mod.speak("halo", str(d / "out.wav"), backend="piper")
        self.assertFalse(d.exists())

    def test_speak_espeak_rejects_dash_voice(self):
        # V4: voice yang diawali "-" akan diparse sebagai flag CLI oleh
        # espeak-ng — ditolak seperti guard di synthesize().
        out = Path(tempfile.mkdtemp()) / "out.wav"
        with self.assertRaisesRegex(voice_mod.VoiceError, "tidak boleh diawali"):
            voice_mod._speak_espeak("halo", out, voice="--proxy", binary="espeak-ng")


class VoiceStatusTests(unittest.TestCase):
    def test_status_shape(self):
        st = voice_mod.voice_status()
        self.assertIn("stt", st)
        self.assertIn("tts_local", st)
        self.assertIn("tts_edge", st)
        for v in st.values():
            self.assertIn("available", v)
            self.assertIn("detail", v)

    def test_download_model_bad_name(self):
        with self.assertRaisesRegex(voice_mod.VoiceError, "tidak dikenal"):
            voice_mod.download_stt_model("xxl")

    def test_download_model_no_package(self):
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaisesRegex(voice_mod.VoiceError, "faster-whisper"):
                voice_mod.download_stt_model("tiny")


if __name__ == "__main__":
    unittest.main()
