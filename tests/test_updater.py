"""Contract tests for the in-place `zeline update` command."""
from __future__ import annotations

import hashlib
import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = importlib.import_module("zeline.updater")

    def test_cli_exposes_update_command(self):
        cli = importlib.import_module("zeline.cli")
        parser = cli.build_parser()
        namespace = parser.parse_args(["update"])
        self.assertEqual(namespace.command, "update")
        upgrade = parser.parse_args(["upgrade"])
        self.assertEqual(upgrade.command, "upgrade")

    def test_checkout_mode_uses_local_installer_source(self):
        fake_root = Path("/tmp/zeline-checkout")
        installer = fake_root / self.updater._installer_name()
        with mock.patch.object(self.updater, "_checkout_root", return_value=fake_root), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.updater, "_run_installer", return_value=0) as run:
            code = self.updater.update()
        self.assertEqual(code, 0)
        run.assert_called_once_with(installer, fake_root)

    def test_release_mode_verifies_checksum_before_running(self):
        installer_name = self.updater._installer_name()
        installer_bytes = b"#!/usr/bin/env bash\necho updated\n"
        digest = hashlib.sha256(installer_bytes).hexdigest()
        sums = f"{digest}  {installer_name}\n0000  zeline-9.9.9-py3-none-any.whl\n"

        def fake_get(url, accept=""):
            if url.endswith("SHA256SUMS"):
                return sums.encode()
            if url.endswith(installer_name):
                return installer_bytes
            raise AssertionError(url)

        with mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", return_value="v9.9.9"), \
             mock.patch.object(self.updater, "_https_get", side_effect=fake_get), \
             mock.patch.object(self.updater, "_run_installer", return_value=0) as run:
            code = self.updater.update()
        self.assertEqual(code, 0)
        run.assert_called_once()

    def test_release_mode_refuses_on_checksum_mismatch(self):
        installer_name = self.updater._installer_name()

        def fake_get(url, accept=""):
            if url.endswith("SHA256SUMS"):
                return (b"a" * 64 + f"  {installer_name}\n".encode())
            if url.endswith(installer_name):
                return b"tampered installer"
            raise AssertionError(url)

        with mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", return_value="v9.9.9"), \
             mock.patch.object(self.updater, "_https_get", side_effect=fake_get), \
             mock.patch.object(self.updater, "_run_installer", return_value=0) as run:
            code = self.updater.update()
        self.assertEqual(code, 1)
        run.assert_not_called()

    def test_https_get_rejects_non_https(self):
        with self.assertRaises(ValueError):
            self.updater._https_get("http://insecure.example/install.sh")


class UpdaterCrossPlatformTests(unittest.TestCase):
    """`zeline update` must be one command on every supported platform."""

    def setUp(self) -> None:
        self.updater = importlib.import_module("zeline.updater")

    def test_posix_uses_bash_install_sh(self):
        with mock.patch.object(self.updater, "_is_windows", return_value=False):
            self.assertEqual(self.updater._installer_name(), "install.sh")
            command = self.updater._installer_command(Path("/tmp/install.sh"), None)
        self.assertEqual(command, ["bash", "/tmp/install.sh"])

    def test_posix_source_mode_passes_double_dash_source(self):
        with mock.patch.object(self.updater, "_is_windows", return_value=False):
            command = self.updater._installer_command(Path("/repo/install.sh"), Path("/repo"))
        self.assertEqual(command, ["bash", "/repo/install.sh", "--source", "/repo"])

    def test_windows_uses_powershell_install_ps1(self):
        with mock.patch.object(self.updater, "_is_windows", return_value=True), \
             mock.patch.object(self.updater, "_powershell_bin", return_value="pwsh"):
            self.assertEqual(self.updater._installer_name(), "install.ps1")
            command = self.updater._installer_command(Path(r"C:\tmp\install.ps1"), None)
        self.assertEqual(
            command,
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", r"C:\tmp\install.ps1"],
        )

    def test_windows_source_mode_passes_pascal_case_source(self):
        with mock.patch.object(self.updater, "_is_windows", return_value=True), \
             mock.patch.object(self.updater, "_powershell_bin", return_value="powershell"):
            command = self.updater._installer_command(Path(r"C:\repo\install.ps1"), Path(r"C:\repo"))
        self.assertEqual(command[-2:], ["-Source", r"C:\repo"])
        self.assertEqual(command[0], "powershell")

    def test_windows_release_mode_downloads_ps1_asset(self):
        installer_bytes = b"Write-Host updated\n"
        digest = hashlib.sha256(installer_bytes).hexdigest()
        sums = f"{digest}  install.ps1\n"
        seen: list[str] = []

        def fake_get(url, accept=""):
            seen.append(url)
            if url.endswith("SHA256SUMS"):
                return sums.encode()
            if url.endswith("install.ps1"):
                return installer_bytes
            raise AssertionError(url)

        with mock.patch.object(self.updater, "_is_windows", return_value=True), \
             mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", return_value="v9.9.9"), \
             mock.patch.object(self.updater, "_https_get", side_effect=fake_get), \
             mock.patch.object(self.updater, "_run_installer", return_value=0) as run:
            code = self.updater.update()
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertTrue(any(url.endswith("/install.ps1") for url in seen))
        self.assertFalse(any(url.endswith("/install.sh") for url in seen))

    def test_powershell_bin_prefers_pwsh_when_available(self):
        with mock.patch.object(self.updater.shutil, "which", side_effect=lambda name: name == "pwsh"):
            self.assertEqual(self.updater._powershell_bin(), "pwsh")
        with mock.patch.object(self.updater.shutil, "which", return_value=None):
            self.assertEqual(self.updater._powershell_bin(), "powershell")


class UpdateDocumentationTests(unittest.TestCase):
    """The short update path must be documented for every platform."""

    def test_public_docs_document_the_one_command_update(self):
        pages = [
            SOURCE_ROOT / "README.md",
            SOURCE_ROOT / "docs" / "README.id.md",
            SOURCE_ROOT / "docs" / "README.zh.md",
            SOURCE_ROOT / "docs" / "installation.md",
        ]
        for page in pages:
            text = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertIn("zeline update", text)

    def test_install_page_states_update_works_on_all_platforms(self):
        page = (SOURCE_ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
        self.assertIn("zeline update", page)
        for platform in ("Termux", "Linux", "macOS", "Windows"):
            self.assertIn(platform, page)
        self.assertIn("~/.zeline", page)


if __name__ == "__main__":
    unittest.main()
