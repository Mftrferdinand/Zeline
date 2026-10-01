"""Contract tests for the in-place `zeline update` command."""
from __future__ import annotations

import hashlib
import http.client
import importlib
import io
import sys
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _stub_gateway_service(
    *,
    active: bool,
    state: dict | None = None,
    recorder: mock.MagicMock | None = None,
) -> types.ModuleType:
    """A fake ``zeline.gateway_service`` for tests that call ``update()``.

    Without this, ``update()`` reaches the REAL service: it drains and kills the
    gateway the operator is actually running, then relaunches it from a test
    process. Observed live -- the suite left `Gateway is not running.` behind and
    a stale PID that `start()` then refused to replace. A unit test must never
    touch the machine's running services.
    """
    service = recorder or mock.MagicMock()
    service.status.return_value = (active, "running" if active else "not running", state)
    service.drain_then_stop.return_value = (True, "stopped")
    service.start.return_value = (True, "started")
    module = types.ModuleType("zeline.gateway_service")
    module.status = service.status
    module.drain_then_stop = service.drain_then_stop
    module.start = service.start
    return module


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.updater = importlib.import_module("zeline.updater")
        # Every test in this class calls update(); none of them is about gateway
        # lifecycle, so the service is stubbed as "nothing running" for all of
        # them. See _stub_gateway_service for what this prevents.
        patcher = mock.patch.dict(
            sys.modules, {"zeline.gateway_service": _stub_gateway_service(active=False)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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


class GatewaySelectionAcrossUpdateTests(unittest.TestCase):
    """An update must give back the gateways the operator was running.

    ``drain_then_stop`` deletes the state file, so the selection only exists
    between ``status()`` and the restart. Losing it meant an operator who ran
    ``gateway start --only telegram`` got every enabled gateway back after
    ``zeline update`` -- WhatsApp and Discord silently launched by an unrelated
    command, on a phone, with no indication why.
    """

    def setUp(self) -> None:
        self.updater = importlib.import_module("zeline.updater")

    def _run_update_with_gateway(self, state):
        """Run a successful checkout-mode update against a stubbed service."""
        service = mock.MagicMock()
        module = _stub_gateway_service(active=True, state=state, recorder=service)
        with mock.patch.dict(sys.modules, {"zeline.gateway_service": module}), \
             mock.patch.object(self.updater, "_checkout_root", return_value=Path("/tmp/zl")), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.updater, "_run_installer", return_value=0):
            code = self.updater.update()
        self.assertEqual(code, 0)
        return service

    def test_a_single_gateway_selection_survives_the_update(self):
        service = self._run_update_with_gateway({"pid": 123, "only": ["telegram"]})
        service.start.assert_called_once_with(["telegram"])

    def test_several_selected_gateways_all_come_back(self):
        service = self._run_update_with_gateway({"pid": 123, "only": ["telegram", "discord"]})
        service.start.assert_called_once_with(["telegram", "discord"])

    def test_an_unrestricted_gateway_restarts_unrestricted(self):
        """``only: []`` means "all enabled" and must not become a literal []."""
        service = self._run_update_with_gateway({"pid": 123, "only": []})
        service.start.assert_called_once_with(None)

    def test_nothing_running_means_nothing_started(self):
        service = mock.MagicMock()
        module = _stub_gateway_service(active=False, recorder=service)
        with mock.patch.dict(sys.modules, {"zeline.gateway_service": module}), \
             mock.patch.object(self.updater, "_checkout_root", return_value=Path("/tmp/zl")), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.updater, "_run_installer", return_value=0):
            code = self.updater.update()
        self.assertEqual(code, 0)
        service.drain_then_stop.assert_not_called()
        service.start.assert_not_called()

    def test_the_selection_is_read_before_the_state_file_is_deleted(self):
        """Order is the whole bug: status() must be called before the stop.

        ``drain_then_stop`` unlinks the state file, so reading the selection
        afterwards can only ever return "all enabled".
        """
        calls: list[str] = []
        module = types.ModuleType("zeline.gateway_service")
        module.status = lambda: (calls.append("status") or (True, "running", {"pid": 1, "only": ["telegram"]}))
        module.drain_then_stop = lambda: (calls.append("stop") or (True, "stopped"))
        module.start = lambda only=None: (calls.append(f"start:{only}") or (True, "started"))
        with mock.patch.dict(sys.modules, {"zeline.gateway_service": module}), \
             mock.patch.object(self.updater, "_checkout_root", return_value=Path("/tmp/zl")), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.updater, "_run_installer", return_value=0):
            self.updater.update()
        # Health-check call setelah start menambah satu "status" di akhir.
        self.assertEqual(calls, ["status", "stop", "start:['telegram']", "status"])

    def test_a_failed_update_still_restores_the_same_selection(self):
        """A restart after a failure must not widen what was running either."""
        service = mock.MagicMock()
        module = _stub_gateway_service(
            active=True, state={"pid": 9, "only": ["telegram"]}, recorder=service
        )
        with mock.patch.dict(sys.modules, {"zeline.gateway_service": module}), \
             mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", side_effect=RuntimeError("offline")):
            code = self.updater.update()
        self.assertEqual(code, 1)
        service.start.assert_called_once_with(["telegram"])


class UpdaterCrossPlatformTests(unittest.TestCase):
    """`zeline update` must be one command on every supported platform."""

    def setUp(self) -> None:
        self.updater = importlib.import_module("zeline.updater")
        # Two tests in this class call update(); without the stub the real
        # gateway_service would drain and kill the operator's actual gateway
        # whenever the suite runs on a machine that has one running.
        patcher = mock.patch.dict(
            sys.modules, {"zeline.gateway_service": _stub_gateway_service(active=False)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_posix_uses_bash_install_sh(self):
        installer = Path("/tmp/install.sh")
        with mock.patch.object(self.updater, "_is_windows", return_value=False):
            self.assertEqual(self.updater._installer_name(), "install.sh")
            command = self.updater._installer_command(installer, None)
        self.assertEqual(command, ["bash", str(installer)])

    def test_posix_source_mode_passes_double_dash_source(self):
        installer = Path("/repo/install.sh")
        source = Path("/repo")
        with mock.patch.object(self.updater, "_is_windows", return_value=False):
            command = self.updater._installer_command(installer, source)
        self.assertEqual(command, ["bash", str(installer), "--source", str(source)])

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


class DownloadRetryTests(unittest.TestCase):
    """A flaky connection must not fail `/update`: the updater retries the GET.

    The failure this pins: a single ``RemoteDisconnected`` halfway through the
    installer download used to abort the whole update with exit 1, forcing the
    owner to drain the gateway and run `/update` again by hand.
    """

    def setUp(self) -> None:
        self.updater = importlib.import_module("zeline.updater")

    def _retry(self, effects):
        """Run ``_https_get_retry`` with a scripted ``_https_get`` and no real sleeps."""
        with mock.patch.object(self.updater, "_https_get", side_effect=effects) as get, \
             mock.patch("time.sleep") as sleep:
            try:
                outcome = self.updater._https_get_retry("https://example.com/install.sh")
            except Exception as exc:  # noqa: BLE001 — the tests assert on the raised error
                outcome = exc
        return get, sleep, outcome

    def test_a_dropped_connection_is_retried_until_it_succeeds(self):
        get, sleep, outcome = self._retry(
            [
                http.client.RemoteDisconnected("closed"),
                http.client.RemoteDisconnected("closed"),
                b"installer-bytes",
            ]
        )
        self.assertEqual(outcome, b"installer-bytes")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(2.0), mock.call(4.0)])

    def test_it_gives_up_after_all_attempts(self):
        get, sleep, outcome = self._retry(http.client.RemoteDisconnected("closed"))
        self.assertIsInstance(outcome, http.client.RemoteDisconnected)
        self.assertEqual(get.call_count, self.updater.DOWNLOAD_ATTEMPTS)
        self.assertEqual(sleep.call_count, self.updater.DOWNLOAD_ATTEMPTS - 1)

    def test_a_404_is_not_retried(self):
        """A missing asset will not appear on the second try."""
        error = urllib.error.HTTPError(
            "https://example.com/install.sh", 404, "Not Found", {}, io.BytesIO(b"")
        )
        get, sleep, outcome = self._retry(error)
        self.assertIs(outcome, error)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_a_503_is_retried(self):
        error = urllib.error.HTTPError(
            "https://example.com/install.sh", 503, "Service Unavailable", {}, io.BytesIO(b"")
        )
        get, _sleep, outcome = self._retry([error, b"installer-bytes"])
        self.assertEqual(outcome, b"installer-bytes")
        self.assertEqual(get.call_count, 2)

    def test_a_programming_error_is_never_retried(self):
        get, sleep, outcome = self._retry(ValueError("refusing non-HTTPS URL"))
        self.assertIsInstance(outcome, ValueError)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_update_reports_the_attempt_count_when_the_download_keeps_failing(self):
        """The owner reading the log should see that it retried, not just failed."""
        module = _stub_gateway_service(active=False)
        with mock.patch.dict(sys.modules, {"zeline.gateway_service": module}), \
             mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", return_value="v9.9.9"), \
             mock.patch.object(
                 self.updater, "_https_get", side_effect=http.client.RemoteDisconnected("closed")
             ), \
             mock.patch("time.sleep"), \
             mock.patch("builtins.print") as say:
            code = self.updater.update()
        self.assertEqual(code, 1)
        lines = " ".join(call.args[0] for call in say.call_args_list)
        self.assertIn(f"after {self.updater.DOWNLOAD_ATTEMPTS} attempts", lines)
        self.assertIn("RemoteDisconnected", lines)


if __name__ == "__main__":
    unittest.main()
