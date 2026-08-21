"""Cross-platform installer branding and platform-support contracts."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.2.3"
RELEASE_TAG = f"v{RELEASE_VERSION}"


class ReleaseVersionContractTests(unittest.TestCase):
    def test_public_urls_use_canonical_repo_name(self):
        """Nama repo lama cuma hidup lewat redirect GitHub — jangan diandalkan."""
        pages = [
            ROOT / "README.md",
            ROOT / "docs" / "README.id.md",
            ROOT / "docs" / "README.zh.md",
            ROOT / "docs" / "installation.md",
            ROOT / "install.sh",
            ROOT / "install.ps1",
            ROOT / "pyproject.toml",
            ROOT / ".github" / "RELEASE_NOTES.md",
        ]
        for page in pages:
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertNotIn("Mftrferdinand/Zerolinear", text)

    def test_package_installers_and_public_docs_target_v021(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        package_init = (ROOT / "zeline" / "__init__.py").read_text(encoding="utf-8")
        posix = (ROOT / "install.sh").read_text(encoding="utf-8")
        windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn(f'version = "{RELEASE_VERSION}"', pyproject)
        self.assertIn(f'__version__ = "{RELEASE_VERSION}"', package_init)
        self.assertIn(f'VERSION="{RELEASE_VERSION}"', posix)
        self.assertIn(f'REF="{RELEASE_TAG}"', posix)
        self.assertIn(f"$Version     = '{RELEASE_VERSION}'", windows)
        self.assertIn(f"$ReleaseRef  = '{RELEASE_TAG}'", windows)
        for relative in ("README.md", "docs/README.id.md", "docs/README.zh.md", "docs/installation.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(f"releases/download/{RELEASE_TAG}", text, relative)
            self.assertNotIn("releases/download/v0.2.1", text, relative)
            self.assertNotIn("releases/download/v0.2.0", text, relative)


@unittest.skipIf(os.name == "nt", "install.sh is verified by POSIX/macOS jobs; Windows uses install.ps1")
class PosixInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = ROOT / "install.sh"
        self.text = self.script.read_text(encoding="utf-8")

    def test_installer_has_explicit_termux_linux_macos_and_ios_ish_platforms(self):
        for platform in ("termux", "linux", "macos", "ios-ish"):
            with self.subTest(platform=platform):
                self.assertIn(platform, self.text.lower())

    def test_banner_uses_one_precise_zeline_identity(self):
        result = subprocess.run(
            ["bash", str(self.script), "--platform-info", "--platform", "linux"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Z  E  L  I  N  E", result.stdout)
        self.assertIn("AGENTIC AI BY ZEROLINEAR • v0.2.3", result.stdout)
        self.assertIn("╭", result.stdout)
        self.assertIn("╰", result.stdout)

    def test_installer_supports_platform_probe_without_installing(self):
        for platform in ("termux", "linux", "macos", "ios-ish"):
            with self.subTest(platform=platform):
                result = subprocess.run(
                    ["bash", str(self.script), "--platform-info", "--platform", platform],
                    cwd=ROOT,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(platform, result.stdout.lower())
                self.assertIn("Z  E  L  I  N  E", result.stdout)

    def test_installer_can_install_into_an_isolated_prefix(self):
        """Exercise the real POSIX installer without touching the developer home."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = subprocess.run(
                [
                    "bash",
                    str(self.script),
                    "--source",
                    str(ROOT),
                    "--install-root",
                    str(root / "runtime"),
                    "--bin-dir",
                    str(root / "bin"),
                    "--no-seed",
                    "--platform",
                    "linux",
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            command = root / "bin" / "zeline"
            self.assertTrue(command.exists())
            version = subprocess.run(
                [str(command), "--version"],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("zeline 0.2.3", version.stdout.lower())

    def test_installer_wrapper_handles_spaces_quotes_and_dollar_in_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "zeline path 'quoted' $cash"
            result = subprocess.run(
                [
                    "bash", str(self.script),
                    "--source", str(ROOT),
                    "--install-root", str(root / "runtime"),
                    "--bin-dir", str(root / "bin"),
                    "--no-seed", "--platform", "linux",
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            command = root / "bin" / "zeline"
            version = subprocess.run(
                [str(command), "--version"],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)

    def test_installer_rejects_newlines_in_path_arguments(self):
        result = subprocess.run(
            [
                "bash", str(self.script), "--platform-info", "--platform", "linux",
                "--install-root", "bad\npath",
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("newline", result.stderr.lower())

    def test_remote_source_is_an_immutable_release_not_main(self):
        self.assertIn('REF="v0.2.3"', self.text)
        self.assertNotIn('BRANCH="main"', self.text)

    def test_release_downloads_require_hardened_curl(self):
        self.assertIn("--proto '=https'", self.text)
        self.assertIn("--tlsv1.2", self.text)
        self.assertNotIn("wget -qO", self.text)

    def test_posix_installer_rejects_malformed_expected_digest(self):
        self.assertIn("expected digest is not 64 hexadecimal characters", self.text)
        self.assertIn('*[!0-9a-fA-F]*)', self.text)
        self.assertIn('[ "${#expected}" -eq 64 ]', self.text)

    def test_local_checkout_requires_explicit_source_option(self):
        self.assertIn('[ -n "$SOURCE_OVERRIDE" ] || return 0', self.text)
        self.assertIn('SOURCE_DIR="$(CDPATH= cd -- "$SOURCE_OVERRIDE" && pwd)"', self.text)
        self.assertNotIn('BASH_SOURCE[0]', self.text)


class PowerShellInstallerBrandTests(unittest.TestCase):
    def test_windows_installer_matches_the_zeline_boxed_identity(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("Z  E  L  I  N  E", text)
        self.assertIn("AGENTIC AI BY ZEROLINEAR", text)
        # Windows PowerShell 5.1 decodes UTF-8-without-BOM as ANSI. Construct
        # Unicode at runtime rather than storing raw box glyphs in the script.
        self.assertIn("0x256D", text)
        self.assertIn("0x2570", text)
        self.assertIn("[switch]$PlatformInfo", text)
        self.assertIn("Windows PowerShell", text)

    def test_windows_remote_install_uses_versioned_checksum_verified_wheel(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("v0.2.3", text)
        self.assertIn("SHA256SUMS", text)
        self.assertIn("Get-FileHash -Algorithm SHA256", text)
        self.assertIn("expected digest is not 64 hexadecimal characters", text)
        self.assertIn("-notmatch '^[0-9a-f]{64}$'", text)
        self.assertNotIn("git clone --depth 1 --branch", text)

    def test_windows_local_checkout_requires_explicit_source_option(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$Source", text)
        self.assertNotIn("$PSScriptRoot", text)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_release_tag_must_come_from_merged_main_history(self):
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("git fetch origin main", text)
        self.assertIn('git merge-base --is-ancestor "$GITHUB_SHA" origin/main', text)

    def test_release_notes_are_zeline_controlled_not_history_generated(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        notes = (ROOT / ".github" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertNotIn("generate_release_notes: true", workflow)
        self.assertIn("body_path: .github/RELEASE_NOTES.md", workflow)
        self.assertIn("name: Zeline ${{ github.ref_name }}", workflow)
        self.assertNotIn("aes" + "ora", notes.casefold())
        self.assertIn("Zeline", notes)

    def test_release_audits_wheel_and_sdist_without_optimized_asserts(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("import tarfile", workflow)
        self.assertIn("zipfile.ZipFile", workflow)
        self.assertIn("tarfile.open", workflow)
        for marker in ("__pycache__", ".pytest_cache", ".zeline", ".env", "tmdb-media-web-maintenance"):
            self.assertIn(marker, workflow)
        self.assertIn('part == ".env"', workflow)
        self.assertIn('PACKAGE_VERSION=', workflow)
        self.assertIn('os.environ["PACKAGE_VERSION"]', workflow)
        self.assertNotIn('expected_version = "0.2.3"', workflow)
        self.assertIn("raise SystemExit", workflow)
        self.assertNotIn("assert not blocked", workflow)

    def test_release_notes_link_to_immutable_tag(self):
        notes = (ROOT / ".github" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("blob/v0.2.3/docs/installation.md", notes)
        self.assertNotIn("blob/main/docs/installation.md", notes)


class InstallationPageTests(unittest.TestCase):
    def test_dedicated_install_page_covers_every_supported_platform(self):
        page = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
        for heading in ("Termux", "Linux", "macOS", "iOS", "Windows PowerShell"):
            with self.subTest(heading=heading):
                self.assertIn(heading, page)
        self.assertIn("install.sh", page)
        self.assertIn("install.ps1", page)

    def test_install_docs_use_versioned_release_assets_not_pipe_from_main(self):
        pages = [
            ROOT / "README.md",
            ROOT / "docs" / "README.id.md",
            ROOT / "docs" / "README.zh.md",
            ROOT / "docs" / "installation.md",
        ]
        for page in pages:
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertNotIn("raw.githubusercontent.com/Mftrferdinand/Zeline/main/install", text)
                self.assertNotIn("| bash", text)
                self.assertNotIn("| iex", text.lower())
                self.assertIn("v0.2.3", text)
                self.assertIn("SHA256SUMS", text)
                self.assertNotIn("assert actual == expected", text)
                self.assertIn("raise SystemExit", text)
                if "powershell" in text.casefold():
                    self.assertIn("-notmatch '^[0-9a-f]{64}$'", text)
        self.assertIn("SHA256SUMS", (ROOT / "docs" / "installation.md").read_text(encoding="utf-8"))

    def test_checkout_docs_require_explicit_source_mode(self):
        page = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
        self.assertIn("bash install.sh --source .", page)
        self.assertIn("install.ps1 -Source .", page)
        self.assertIn("exact selected-interpreter command", page)
        self.assertNotIn("You can always\nrun it through", page)

    def test_public_readmes_have_no_broken_local_markdown_links(self):
        pages = [
            ROOT / "README.md",
            ROOT / "docs" / "README.id.md",
            ROOT / "docs" / "README.zh.md",
            ROOT / "docs" / "installation.md",
        ]
        broken: list[str] = []
        for page in pages:
            text = page.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if "://" in target or target.startswith(("#", "mailto:")):
                    continue
                local = target.split("#", 1)[0]
                if local and not (page.parent / local).resolve().exists():
                    broken.append(f"{page.relative_to(ROOT)} -> {target}")
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)