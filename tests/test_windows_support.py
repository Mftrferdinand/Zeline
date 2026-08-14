"""Cross-platform (Windows) safety tests.

These lock in the fixes that made `zeline` installable and runnable from
PowerShell. Each test simulates Windows on this POSIX machine, because the
failures they guard against are import-time or API-shape errors that do not
need a real Windows kernel to detect.

Guarded regressions:
1. `import zeline.cli` must not require termios/tty (Windows has neither).
2. Windows arrow keys (0x00/0xE0 prefix) must decode to up/down.
3. `raw_mode()` must be a no-op on Windows, not a crash.
4. `_pid_alive` must NEVER call os.kill on Windows -- there os.kill(pid, 0)
   calls TerminateProcess and KILLS the process it is meant to probe.
5. Gateway spawn must use `creationflags` on Windows, not `start_new_session`
   (which is POSIX-only and raises ValueError on Windows).
"""

from __future__ import annotations

import builtins
import importlib
import sys
import types
import unittest
from unittest import mock

POSIX_ONLY_MODULES = {"termios", "tty", "fcntl", "pwd", "grp"}


class WindowsImportTests(unittest.TestCase):
    """The CLI must import with POSIX-only modules unavailable."""

    def test_cli_imports_without_posix_only_modules(self):
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in POSIX_ONLY_MODULES:
                raise ModuleNotFoundError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        saved = {
            name: module
            for name, module in sys.modules.items()
            if name.startswith("zeline") or name.split(".")[0] in POSIX_ONLY_MODULES
        }
        for name in saved:
            del sys.modules[name]
        try:
            with mock.patch.object(builtins, "__import__", guarded_import):
                cli = importlib.import_module("zeline.cli")
                self.assertTrue(hasattr(cli, "main"))
        finally:
            for name in list(sys.modules):
                if name.startswith("zeline"):
                    del sys.modules[name]
            sys.modules.update(saved)

    def test_no_top_level_posix_terminal_imports_in_package(self):
        """Only _termkey may touch termios/tty, and only inside functions."""
        from pathlib import Path

        package_dir = Path(str(importlib.import_module("zeline").__file__)).parent
        offenders = []
        for path in package_dir.rglob("*.py"):
            if path.name == "_termkey.py":
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped in {"import termios", "import tty"} and not line.startswith(" "):
                    offenders.append(f"{path.name}:{number}: {stripped}")
        self.assertEqual(offenders, [], f"top-level POSIX terminal imports: {offenders}")


class WindowsKeyDecodingTests(unittest.TestCase):
    """Windows sends arrows as a two-byte prefixed sequence, not ANSI escapes."""

    def setUp(self):
        self.termkey = importlib.import_module("zeline._termkey")

    def test_windows_arrow_keys_decode_to_up_and_down(self):
        keys = iter(["\x00", "P", "\xe0", "H", "\r"])
        with mock.patch.object(self.termkey, "IS_WINDOWS", True), \
             mock.patch.object(self.termkey, "read_key", lambda: next(keys)):
            self.assertEqual(self.termkey.read_menu_key(), "down")
            self.assertEqual(self.termkey.read_menu_key(), "up")
            self.assertEqual(self.termkey.read_menu_key(), "enter")

    def test_posix_arrow_keys_still_decode(self):
        keys = iter(["\x1b", "[", "B", "\x1b", "[", "A"])
        with mock.patch.object(self.termkey, "IS_WINDOWS", False), \
             mock.patch.object(self.termkey, "read_key", lambda: next(keys)):
            self.assertEqual(self.termkey.read_menu_key(), "down")
            self.assertEqual(self.termkey.read_menu_key(), "up")

    def test_ctrl_c_raises_keyboard_interrupt_on_both_platforms(self):
        for is_windows in (True, False):
            with mock.patch.object(self.termkey, "IS_WINDOWS", is_windows), \
                 mock.patch.object(self.termkey, "read_key", lambda: "\x03"):
                with self.assertRaises(KeyboardInterrupt):
                    self.termkey.read_menu_key()

    def test_raw_mode_is_noop_on_windows(self):
        with mock.patch.object(self.termkey, "IS_WINDOWS", True):
            with self.termkey.raw_mode():
                pass  # Must not raise even with no termios available.


class WindowsProcessTests(unittest.TestCase):
    """Gateway lifecycle must not use POSIX-only process APIs on Windows."""

    def setUp(self):
        self.service = importlib.import_module("zeline.gateway_service")

    def test_pid_alive_never_calls_os_kill_on_windows(self):
        """os.kill(pid, 0) TERMINATES the process on Windows. Must not be used."""
        with mock.patch.object(self.service, "IS_WINDOWS", True), \
             mock.patch.object(self.service._winproc, "pid_alive", return_value=True) as probe, \
             mock.patch("os.kill") as os_kill:
            self.assertTrue(self.service._pid_alive(4321))
        probe.assert_called_once_with(4321)
        os_kill.assert_not_called()

    def test_start_token_uses_creation_time_on_windows(self):
        with mock.patch.object(self.service, "IS_WINDOWS", True), \
             mock.patch.object(self.service._winproc, "creation_token", return_value="wincreate:99"):
            self.assertEqual(self.service._process_start_token(1234), "wincreate:99")

    def test_sigkill_maps_to_taskkill_tree_on_windows(self):
        import signal

        with mock.patch.object(self.service, "IS_WINDOWS", True), \
             mock.patch.object(self.service._winproc, "terminate_tree", return_value=True) as taskkill:
            self.assertTrue(self.service._signal_process(777, signal.SIGKILL))
        taskkill.assert_called_once_with(777)

    def test_spawn_uses_creationflags_not_start_new_session_on_windows(self):
        """start_new_session is POSIX-only; passing it on Windows raises."""
        from zeline._winproc import CREATION_FLAGS

        captured: dict = {}

        class FakeProcess:
            pid = 4242

        def fake_popen(*args, **kwargs):
            captured.update(kwargs)
            return FakeProcess()

        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log_file = Path(tmp.name) / "gateway.log"

        config = importlib.import_module("zeline.config")
        with mock.patch.object(self.service, "IS_WINDOWS", True), \
             mock.patch.object(self.service, "status", return_value=(False, "", None)), \
             mock.patch.object(config, "GATEWAYS", {"telegram": {"enabled": True}}), \
             mock.patch.object(self.service, "validate_gateway", return_value=[]), \
             mock.patch.object(self.service, "_process_start_token", return_value="wincreate:1"), \
             mock.patch.object(self.service, "_process_start_ticks", return_value=None), \
             mock.patch.object(self.service, "_write_private"), \
             mock.patch.object(self.service.config, "ensure_data_dirs"), \
             mock.patch.object(self.service, "LOG_FILE", log_file), \
             mock.patch("subprocess.Popen", fake_popen):
            ok, message = self.service.start(None)

        self.assertTrue(ok, message)
        self.assertEqual(captured.get("creationflags"), CREATION_FLAGS)
        self.assertNotIn("start_new_session", captured)

    def test_spawn_uses_start_new_session_on_posix(self):
        captured: dict = {}

        class FakeProcess:
            pid = 4243

        def fake_popen(*args, **kwargs):
            captured.update(kwargs)
            return FakeProcess()

        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log_file = Path(tmp.name) / "gateway.log"

        config = importlib.import_module("zeline.config")
        with mock.patch.object(self.service, "IS_WINDOWS", False), \
             mock.patch.object(self.service, "status", return_value=(False, "", None)), \
             mock.patch.object(config, "GATEWAYS", {"telegram": {"enabled": True}}), \
             mock.patch.object(self.service, "validate_gateway", return_value=[]), \
             mock.patch.object(self.service, "_process_start_token", return_value="ticks:1"), \
             mock.patch.object(self.service, "_process_start_ticks", return_value="1"), \
             mock.patch.object(self.service, "_write_private"), \
             mock.patch.object(self.service.config, "ensure_data_dirs"), \
             mock.patch.object(self.service, "LOG_FILE", log_file), \
             mock.patch("subprocess.Popen", fake_popen):
            ok, message = self.service.start(None)

        self.assertTrue(ok, message)
        self.assertTrue(captured.get("start_new_session"))
        self.assertNotIn("creationflags", captured)


class WindowsProcHelperTests(unittest.TestCase):
    """_winproc must degrade safely when kernel32 is absent (i.e. on POSIX)."""

    def setUp(self):
        self.winproc = importlib.import_module("zeline._winproc")

    def test_helpers_return_safe_defaults_without_kernel32(self):
        self.assertFalse(self.winproc.available())
        self.assertFalse(self.winproc.pid_alive(1))
        self.assertIsNone(self.winproc.creation_token(1))

    def test_creation_flags_include_new_process_group(self):
        # CREATE_NEW_PROCESS_GROUP (0x200) is required for CTRL_BREAK to reach
        # the child on `gateway stop`.
        self.assertTrue(self.winproc.CREATION_FLAGS & 0x00000200)


class WindowsInstallerTests(unittest.TestCase):
    """install.ps1 must exist and cover the documented Windows pitfalls."""

    def setUp(self):
        from pathlib import Path

        root = Path(str(importlib.import_module("zeline").__file__)).parent.parent
        self.script = root / "install.ps1"

    def test_installer_exists(self):
        self.assertTrue(self.script.exists(), "install.ps1 is missing")

    def test_installer_handles_known_windows_pitfalls(self):
        if not self.script.exists():
            self.skipTest("install.ps1 not present in this checkout")
        text = self.script.read_text(encoding="utf-8")
        # Microsoft Store python.exe stub must be rejected, not used.
        self.assertIn("WindowsApps", text)
        # pip --user Scripts dir is usually off PATH; installer must handle it.
        self.assertIn("Scripts", text)
        self.assertIn("SetEnvironmentVariable", text)
        # `py -3` is the most reliable launcher on Windows.
        self.assertIn("py -3", text)


class WindowsEncodingTests(unittest.TestCase):
    """Legacy Windows code pages must not crash the CLI on banner output."""

    def setUp(self):
        self.cli = importlib.import_module("zeline.cli")

    def test_banner_glyphs_survive_a_cp1252_stream(self):
        """cp1252 cannot encode the box characters; errors='replace' must save it."""
        import io

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch.object(self.cli.sys, "stdout", stream), \
             mock.patch.object(self.cli.sys, "stderr", stream):
            self.cli._ensure_utf8_stdio()
            # Would raise UnicodeEncodeError under strict cp1252.
            print(self.cli.BANNER_SUBTITLE, file=stream)
            print("\u2500\u2502\u256d\u276f", file=stream)
            stream.flush()
        self.assertEqual(stream.encoding.lower().replace("-", ""), "utf8")

    def test_ensure_utf8_is_safe_when_stream_cannot_reconfigure(self):
        class Dumb:
            pass

        with mock.patch.object(self.cli.sys, "stdout", Dumb()), \
             mock.patch.object(self.cli.sys, "stderr", Dumb()):
            self.cli._ensure_utf8_stdio()  # must not raise


if __name__ == "__main__":
    unittest.main()
