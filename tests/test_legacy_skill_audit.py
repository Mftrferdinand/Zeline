"""Regression coverage for runnable legacy skill companions, not corpus exemptions."""
import hashlib
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "zeline" / "skills"


class LegacySkillAuditTests(unittest.TestCase):
    def test_repaired_legacy_scripts_resolve(self):
        audit = runpy.run_path(str(ROOT / "tests/test_bundled_skill_references.py"))
        names = {"video-frames", "tmux", "manim-video"}
        missing = [item for item in audit["_unresolved"]()
                   if item[0].split("/")[0].removesuffix(".md") in names
                   and Path(item[1]).suffix in audit["SCRIPT_SUFFIXES"]]
        self.assertEqual(missing, [])

    def test_flat_upgrade_digests_are_registered(self):
        from zeline.skills import RETIRED_BUNDLED_SKILL_DIGESTS
        expected = {
            "tmux.md": ("63ebd390fbbcd4c0c077420357206e393c85646ab666ce1276f5d49ecc60344d",
                        "29cf2e4001d7fdfac7405a2e39ad373f653a3d2f191d87fcf3bb003d0a8fb4bc"),
            "video-frames.md": ("53a585326838c47d8a501143c94963853a1d91d04a53cf9123a1b5ef833a68d7",
                                "f9a99c0c6f30fd4d5844e3f862d7a0dd03fa2d2c2a788b78fd3ad95d2fadfb61"),
        }
        for filename, digests in expected.items():
            registered = RETIRED_BUNDLED_SKILL_DIGESTS[hashlib.sha256(filename.encode()).hexdigest()]
            for digest in digests:
                self.assertIn(digest, registered)

    def test_video_frame_extraction(self):
        bash = shutil.which("bash")
        ffmpeg = shutil.which("ffmpeg")
        if not bash or not ffmpeg or os.name == "nt":
            self.skipTest("requires native bash and ffmpeg")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input video.mp4"
            output = Path(directory) / "frame output.png"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=64x48:r=2", "-t", "1", "-c:v", "mpeg4",
                            str(source)], check=True, capture_output=True)
            helper = SKILLS / "video-frames/scripts/frame.sh"
            for extra in ([], ["--time", "00:00:00"], ["--index", "1"]):
                with self.subTest(extra=extra):
                    output.unlink(missing_ok=True)
                    result = subprocess.run([bash, str(helper), str(source), *extra,
                                             "--out", str(output)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_tmux_helpers_on_isolated_server(self):
        bash, tmux = shutil.which("bash"), shutil.which("tmux")
        if not bash or not tmux or os.name == "nt":
            self.skipTest("requires native bash and tmux")
        with tempfile.TemporaryDirectory() as directory:
            socket = str(Path(directory) / "socket")
            env = dict(os.environ, TMUX=f"{socket},0,0")
            subprocess.run([tmux, "-S", socket, "new-session", "-d", "-s", "audit", bash],
                           check=True, capture_output=True)
            try:
                listed = subprocess.run([bash, str(SKILLS / "tmux/scripts/find-sessions.sh"),
                                         "-S", socket, "-q", "audit"], capture_output=True, text=True)
                self.assertEqual(listed.returncode, 0, listed.stderr)
                self.assertIn("audit (detached, started ", listed.stdout)
                subprocess.run([tmux, "-S", socket, "send-keys", "-t", "audit", "-l",
                                "printf 'legacy-audit-ready\\n'"], check=True)
                subprocess.run([tmux, "-S", socket, "send-keys", "-t", "audit", "Enter"], check=True)
                waited = subprocess.run([bash, str(SKILLS / "tmux/scripts/wait-for-text.sh"),
                                         "-t", "audit", "-p", "legacy-audit-ready", "-F", "-T", "5"],
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(waited.returncode, 0, waited.stderr)
            finally:
                subprocess.run([tmux, "-S", socket, "kill-server"], capture_output=True)

    def test_helpers_seed_in_clean_home(self):
        with tempfile.TemporaryDirectory() as directory:
            code = "from zeline.skills import seed_skills; seed_skills()"
            subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                           env=dict(os.environ, ZELINE_HOME=directory), check=True, capture_output=True)
            for name, scripts in {"video-frames": ["frame.sh"],
                                  "tmux": ["find-sessions.sh", "wait-for-text.sh"]}.items():
                base = Path(directory) / "skills/public" / name
                self.assertTrue((base / "SKILL.md").is_file())
                for script in scripts:
                    self.assertEqual((base / "scripts" / script).read_bytes(),
                                     (SKILLS / name / "scripts" / script).read_bytes())

    def test_manim_setup_is_explicit_and_not_a_missing_helper(self):
        text = (SKILLS / "manim-video.md").read_text()
        self.assertNotIn("scripts/setup.sh", text)
        self.assertIn("python -m manim --version", text)
        self.assertIn("ffmpeg -version", text)


if __name__ == "__main__":
    unittest.main()
