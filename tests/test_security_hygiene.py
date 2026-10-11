import ast
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ZENITH_SCRIPTS = ROOT / "zeline" / "zenith_tools" / "scripts"
# The one account script that ships. A site-specific twin used to sit beside it
# with a hardcoded signup URL and the maintainer's own referral code -- unusable
# by anyone else, so it was removed rather than genericised twice.
ACCOUNT_SCRIPTS = (
    ROOT / "zeline" / "skills" / "auto-register" / "scripts" / "auto_register.py",
)


class MaskSecretTests(unittest.TestCase):
    """mask_secret is the log/doctor redaction helper -- it must never leak."""

    def test_masks_middle_and_keeps_edges(self):
        from zeline.config import mask_secret

        out = mask_secret("sk-1234567890abcdef")
        self.assertEqual(out, "sk-1…cdef")
        self.assertNotIn("234567890", out)

    def test_short_values_are_fully_masked(self):
        from zeline.config import mask_secret

        self.assertEqual(mask_secret("abcde"), "•••••")
        self.assertEqual(mask_secret("abcdefgh"), "••••••••")

    def test_empty(self):
        from zeline.config import mask_secret

        self.assertEqual(mask_secret(""), "(empty)")

    def test_zero_visible_redacts_everything(self):
        # Regression: visible=0 used to skip the full-mask branch and emit
        # "…" + value, printing the whole secret.
        from zeline.config import mask_secret

        for value in ("s", "ab", "secret", "sk-live-4f9a2b7c8d1e"):
            out = mask_secret(value, 0)
            self.assertNotIn(value, out)
            self.assertEqual(out, "•" * len(value))

    def test_negative_visible_never_leaks(self):
        # Regression: a negative visible sliced value[:-3] from the end.
        from zeline.config import mask_secret

        for visible in (-1, -3, -99):
            out = mask_secret("sk-SECRETCORE-9f2b", visible)
            self.assertNotIn("SECRETCORE-9f2b", out)

class SecurityHygieneTests(unittest.TestCase):
    def test_codeql_workflow_uses_supported_action_generation(self):
        workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
        self.assertIn("github/codeql-action/init@v4", workflow)
        self.assertIn("github/codeql-action/analyze@v4", workflow)
        self.assertNotIn("github/codeql-action/init@v3", workflow)
        self.assertNotIn("github/codeql-action/analyze@v3", workflow)

    def test_tracked_text_has_no_retired_brand_literals(self):
        terms = (
            "her" + "mes", "aes" + "ora", "nous" + " research",
            "nous" + "research", "open" + "claw", "claw" + "hub",
            "super" + "agent", "iron" + "claw", "sel" + "ena",
        )
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT
        ).decode().split("\0")
        offenders = []
        for relative in tracked:
            if not relative:
                continue
            path = ROOT / relative
            try:
                folded = path.read_text(encoding="utf-8").casefold()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            except FileNotFoundError:
                # `git ls-files` reports the index; a file deleted in the working
                # tree but not yet staged is listed and unreadable. Crashing there
                # makes the whole suite unrunnable mid-change instead of reporting
                # the branding problem this test exists to catch.
                continue
            hits = [term for term in terms if term in folded]
            if hits:
                offenders.append(f"{relative}: {','.join(hits)}")
        self.assertEqual(offenders, [])

    def test_bundled_skills_have_no_missing_reference_links(self):
        missing = []
        for path in sorted((ROOT / "zeline" / "skills").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for destination in __import__("re").findall(
                r"\[[^\]]+\]\((references/[^)#]+|editing\.md|pptxgenjs\.md)(?:#[^)]+)?\)",
                text,
            ):
                if not (path.parent / destination).exists():
                    missing.append(f"{path.relative_to(ROOT)} -> {destination}")
        self.assertEqual(missing, [])

    def test_updated_zenith_demo_scripts_execute_without_name_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            # briefing imports alerts, whose live HTTP client is optional and is
            # intentionally absent from the minimal CI test environment. A stub
            # keeps this regression test focused on demo-script execution.
            Path(tmp, "httpx.py").write_text("", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = tmp + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONIOENCODING"] = "utf-8"
            for name in ("automation.py", "briefing.py"):
                completed = subprocess.run(
                    [__import__("sys").executable, str(ZENITH_SCRIPTS / name)],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{name} failed:\nstdout={completed.stdout}\nstderr={completed.stderr}",
                )

    def test_runtime_sources_do_not_use_insecure_tempfile_mktemp(self):
        offenders = []
        for path in sorted(ZENITH_SCRIPTS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "tempfile"
                    and node.func.attr == "mktemp"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_account_scripts_never_print_plaintext_passwords(self):
        offenders = []
        for path in ACCOUNT_SCRIPTS:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    continue
                rendered = ast.unparse(node).casefold()
                if "password" in rendered or "mail_password" in rendered or "mailtm_password" in rendered:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            self.assertIn("os.chmod(OUTPUT_FILE, 0o600)", source)
        self.assertEqual(offenders, [])

    def test_saved_config_is_owner_only(self):
        from zeline import config

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            with mock.patch.object(config, "CONFIG_FILE", target):
                config.save_config({"provider": {"api_key": "secret"}})
            if os.name == "posix":
                mode = stat.S_IMODE(target.stat().st_mode)
                self.assertEqual(mode, 0o600)
            else:
                # Windows uses ACLs and reports synthetic POSIX mode bits.
                self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
