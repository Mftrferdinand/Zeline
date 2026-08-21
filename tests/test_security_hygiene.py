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
ACCOUNT_SCRIPTS = (
    ROOT / "scripts" / "auto_register.py",
    ROOT / "scripts" / "meridian_register.py",
    ROOT / "zeline" / "skills" / "auto-register" / "scripts" / "auto_register.py",
    ROOT / "zeline" / "skills" / "auto-register" / "scripts" / "meridian_register.py",
)


class SecurityHygieneTests(unittest.TestCase):
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
