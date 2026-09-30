"""Integration tests for the edit_video tool (real ffmpeg)."""
import importlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

NEEDS_FFMPEG = shutil.which("ffmpeg") is None


def _make_clip(path: Path, duration: float = 2.0):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=60,
    )


@unittest.skipIf(NEEDS_FFMPEG, "ffmpeg not installed")
class EditVideoTest(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module("zeline.tools")
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        _make_clip(self.ws / "clip.mp4")
        _make_clip(self.ws / "clip2.mp4")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, action, path, **kwargs):
        return self.tools._edit_video(
            action, kwargs.pop("video", "clip.mp4"), path, self.ws,
            kwargs.get("videos", ""), kwargs.get("start", ""), kwargs.get("duration", ""),
            kwargs.get("text", ""), kwargs.get("fontsize", 48), kwargs.get("fontcolor", "white"),
            kwargs.get("position", "bottom"), kwargs.get("audio", ""),
            kwargs.get("volume", 1.0), kwargs.get("factor", 1.0),
        )

    def _assert_mp4(self, name):
        p = self.ws / name
        self.assertTrue(p.is_file(), f"{name} was not created")
        self.assertGreater(p.stat().st_size, 0, f"{name} is empty")

    def test_trim(self):
        out = self._run("trim", "trimmed.mp4", start="0.5", duration="1")
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("trimmed.mp4")

    def test_concat(self):
        out = self._run("concat", "joined.mp4", videos="clip.mp4,clip2.mp4")
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("joined.mp4")

    def test_concat_needs_two(self):
        out = self._run("concat", "joined.mp4", videos="clip.mp4")
        self.assertIn("ERROR", out)

    def test_text_overlay(self):
        out = self._run("text", "captioned.mp4", text="Kota di Atas Awan")
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("captioned.mp4")

    def test_text_overlay_timed(self):
        out = self._run("text", "captioned2.mp4", text="halo", start="0.2", duration="1")
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("captioned2.mp4")

    def test_text_needs_text(self):
        out = self._run("text", "captioned.mp4", text="")
        self.assertIn("ERROR", out)

    def test_audio_replace(self):
        tone = self.ws / "tone.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:a", "pcm_s16le", str(tone)],
            check=True, timeout=60,
        )
        out = self._run("audio", "withaudio.mp4", audio="tone.wav", volume=0.5)
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("withaudio.mp4")

    def test_audio_missing_file(self):
        out = self._run("audio", "withaudio.mp4", audio="nope.mp3")
        self.assertIn("ERROR", out)

    def test_speed_up(self):
        out = self._run("speed", "fast.mp4", factor=2.0)
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("fast.mp4")

    def test_speed_slow(self):
        out = self._run("speed", "slow.mp4", factor=0.5)
        self.assertTrue(out.startswith("OK"), out)
        self._assert_mp4("slow.mp4")

    def test_speed_out_of_range(self):
        out = self._run("speed", "fast.mp4", factor=10.0)
        self.assertIn("ERROR", out)

    def test_unknown_action(self):
        out = self._run("stabilize", "out.mp4")
        self.assertIn("ERROR", out)

    def test_missing_source(self):
        out = self._run("trim", "out.mp4", video="ghost.mp4")
        self.assertIn("ERROR", out)

    def test_rejects_non_mp4_output(self):
        out = self._run("trim", "out.avi")
        self.assertIn("ERROR", out)

    def test_blocks_path_escape(self):
        out = self._run("trim", "../escape.mp4")
        self.assertIn("ERROR", out)
        self.assertFalse((self.ws.parent / "escape.mp4").exists())

    def test_registered_in_workspace_and_full_not_safe(self):
        ws_exec = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=self.ws)
        full_exec = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.ws)
        safe_exec = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.ws)
        for ex in (ws_exec, full_exec):
            self.assertIn("edit_video", {item["function"]["name"] for item in ex.all_schemas})
        self.assertNotIn("edit_video", {item["function"]["name"] for item in safe_exec.all_schemas})

    def test_telegram_progress_label(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        label = telegram._tool_progress_text("edit_video", {"action": "trim"})
        self.assertIn("🎞️", label)
        self.assertIn("trim", label)


if __name__ == "__main__":
    unittest.main()
