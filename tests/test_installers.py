"""Cross-platform installer branding and platform-support contracts."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.2.9"
RELEASE_TAG = f"v{RELEASE_VERSION}"


class ReleaseVersionContractTests(unittest.TestCase):
    def test_declared_release_version_matches_the_package(self):
        """The constant below is the single point of truth for a version bump.

        Without this, RELEASE_VERSION could lag behind pyproject.toml and every
        other assertion in this file would keep passing while checking the wrong
        version. Parsed with a regex rather than tomllib, which only exists from
        3.11 while this package supports 3.10.
        """
        found = re.search(
            r'^version = "([^"]+)"',
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), RELEASE_VERSION)

    def test_runtime_soul_is_declared_as_package_data(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn('"SOUL.md"', pyproject)
        self.assertIn("include zeline/SOUL.md", manifest)

    def test_manifest_ships_the_non_python_files_package_data_misses(self):
        """MANIFEST.in is not redundant with `[tool.setuptools.package-data]`.

        package-data lists globs by extension (`zenith_tools/**/*.py`), so a
        script's companion files — `.env.example`, `requirements.txt`,
        `scope.example.json`, `Dockerfile.sandbox`, `run_tests.sh` — are left
        out of the built distribution. A bundled skill tells the operator to
        `cp .env.example .env` and build that Dockerfile, so dropping the
        recursive-include would ship instructions referring to files that are
        not in the wheel. Measured: six files, all under `zenith_tools/scripts`.
        """
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("recursive-include zeline/zenith_tools *", manifest)
        self.assertIn("recursive-include zeline/skills *", manifest)
        companions = [
            "zenith_tools/scripts/ctf/.env.example",
            "zenith_tools/scripts/ctf/requirements.txt",
            "zenith_tools/scripts/ctf/scope.example.json",
            "zenith_tools/scripts/ctf/sandbox/Dockerfile.sandbox",
            "zenith_tools/scripts/tests/run_tests.sh",
        ]
        missing = [name for name in companions if not (ROOT / "zeline" / name).is_file()]
        self.assertEqual(missing, [], "MANIFEST.in promises files that no longer exist")
        # None of these match a package-data glob, which is why the include is needed.
        package_data = re.search(
            r"^zeline = \[([^\]]+)\]",
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        self.assertIsNotNone(package_data)
        globs = package_data.group(1) if package_data else ""
        self.assertNotIn('zenith_tools/**/*"', globs)

    def test_public_urls_use_canonical_repo_name(self):
        """Nama repo lama cuma hidup lewat redirect GitHub — jangan diandalkan."""
        pages = [
            ROOT / "README.md",
            ROOT / "docs" / "README.id.md",
            ROOT / "docs" / "README.zh.md",
            ROOT / "docs" / "installation.md",
            ROOT / "docs" / "extending.md",
            ROOT / "install.sh",
            ROOT / "install.ps1",
            ROOT / "pyproject.toml",
            ROOT / "CONTRIBUTING.md",
            ROOT / "CHANGELOG.md",
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
        self.assertIn(f"AGENTIC AI BY ZEROLINEAR • {RELEASE_TAG}", result.stdout)
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
            self.assertIn(f"zeline {RELEASE_VERSION}", version.stdout.lower())

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
        self.assertIn(f'REF="{RELEASE_TAG}"', self.text)
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
        self.assertIn(RELEASE_TAG, text)
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
        self.assertNotIn(f'expected_version = "{RELEASE_VERSION}"', workflow)
        self.assertIn("raise SystemExit", workflow)
        self.assertNotIn("assert not blocked", workflow)

    def test_release_notes_link_to_immutable_tag(self):
        notes = (ROOT / ".github" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn(f"blob/{RELEASE_TAG}/docs/installation.md", notes)
        self.assertNotIn("blob/main/docs/installation.md", notes)

    def test_release_verifies_the_wheel_carries_every_skill_tool_and_asset(self):
        """A release ships the ACCUMULATED surface, not only what changed.

        `[tool.setuptools.package-data]` lists globs by extension, so a bundled
        skill that gains a companion file of a new type (a `.sh`, `.json`, or
        `.yaml`) can silently miss the wheel — and then `zeline update` quietly
        removes a working skill from an operator's install. Measured on a clean
        clone: 495 files, 255 skills, 31 tools. The workflow now diffs the built
        wheel against the source tree and fails the release instead of
        publishing a thinner install.
        """
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("Verify the release ships every bundled skill, tool, and asset", workflow)
        self.assertIn("release would ship an incomplete surface", workflow)
        for tree in ("zeline/skills", "zeline/zenith_tools"):
            self.assertIn(tree, workflow)
        # The gate compares against the source tree; a hardcoded expected count
        # would silently pass once the corpus grew.
        self.assertIn("missing_skills = sorted(source_skills - wheel_skills())", workflow)
        self.assertIn("missing_tools = sorted(source_tools - wheel_tools)", workflow)


class BundledSurfaceCompletenessTests(unittest.TestCase):
    """The declared packaging rules must cover the whole bundled corpus.

    Building a wheel takes ~40s and needs network for `build`, so CI's release
    job owns the byte-level check (see
    `ReleaseWorkflowTests.test_release_verifies_the_wheel_carries_every_skill_tool_and_asset`).
    What runs here is the cheap half: every asset extension present in the
    source tree must be reachable by a declared `package-data` glob or by a
    `recursive-include`, which is the condition that makes the wheel complete.
    """

    def _declared_globs(self) -> str:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        found = re.search(r"^zeline = \[([^\]]+)\]", pyproject, re.MULTILINE)
        self.assertIsNotNone(found)
        return found.group(1) if found else ""

    def test_every_bundled_asset_extension_is_covered_by_a_declared_rule(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        recursive_trees = {
            line.split()[1]
            for line in manifest.splitlines()
            if line.startswith("recursive-include ")
        }
        uncovered = []
        for tree in ("zeline/skills", "zeline/zenith_tools"):
            covered_by_include = tree in recursive_trees
            for path in (ROOT / tree).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if covered_by_include:
                    continue
                uncovered.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(uncovered, [], "bundled assets with no packaging rule")

    def test_the_bundled_skill_corpus_is_not_silently_shrinking(self):
        """A floor, not an exact count — new skills are welcome, losses are not."""
        root = ROOT / "zeline" / "skills"
        folders = {path.name for path in root.iterdir() if path.is_dir()}
        singles = {path.name for path in root.glob("*.md")}
        self.assertGreaterEqual(len(folders | singles), 255)
        # The renamed Zenith corpus is the largest single group; #143 fixed the
        # skN → zeline-zenith-zN rename and the ids are stable identifiers.
        self.assertGreaterEqual(len(list(root.glob("zeline-zenith-z*.md"))), 109)

    def test_every_registered_tool_reaches_the_owner_profile(self):
        """A tool nobody can call is a tool that did not ship."""
        import importlib

        tools = importlib.import_module("zeline.tools")
        unreachable = [
            definition.name
            for definition in tools.TOOL_DEFS
            if "full" not in definition.profiles
        ]
        self.assertEqual(unreachable, [])
        self.assertGreaterEqual(len(tools.TOOL_DEFS), 31)


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
                # Never fetch the installer from a moving branch, and never pipe
                # a download straight into a shell.
                self.assertNotIn("raw.githubusercontent.com/Mftrferdinand/Zeline/main/install", text)
                self.assertNotIn("| bash", text)
                self.assertNotIn("| iex", text.lower())
                # Always an immutable tag asset, fetched over pinned HTTPS.
                self.assertIn(f"releases/download/{RELEASE_TAG}/install", text)
                self.assertIn("--proto '=https' --tlsv1.2", text)

    def test_install_docs_do_not_ask_the_reader_to_verify_by_hand(self):
        """A pasted checksum block for install.sh proved nothing.

        It compared the installer against a manifest fetched over the same
        connection from the same release, so whoever could tamper with one could
        tamper with both -- at a cost of ~15 lines in every install snippet. The
        installer verifies the wheel itself, and provenance is the check that
        actually carries an independent signature.
        """
        for page in (ROOT / "README.md", ROOT / "docs" / "installation.md",
                     ROOT / "docs" / "README.id.md", ROOT / "docs" / "README.zh.md"):
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertNotIn("install.sh checksum mismatch", text)
                self.assertNotIn("hashlib.sha256", text)
                self.assertIn("gh attestation verify", text)

    def test_the_installers_still_verify_the_wheel_themselves(self):
        """Dropping the manual step must not drop the real check."""
        posix = (ROOT / "install.sh").read_text(encoding="utf-8")
        windows = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("SHA256SUMS", posix)
        self.assertIn("SHA-256 verification failed", posix)
        self.assertIn("Refusing non-HTTPS download", posix)
        self.assertIn("SHA256SUMS", windows)
        self.assertIn("Get-FileHash -Algorithm SHA256", windows)

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