"""Tests lifecycle service gateway background Zeline."""
from __future__ import annotations

import importlib
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_service(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "zeline" or module_name.startswith("zeline."):
            sys.modules.pop(module_name, None)
    cfg = importlib.import_module("zeline.config")
    service = importlib.import_module("zeline.gateway_service")
    return cfg, service


class GatewayServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("ZELINE_HOME")
        self.home = Path(self.temp.name) / "zeline-home"
        self.config, self.service = fresh_service(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        self.temp.cleanup()

    def test_termux_gateway_acquires_wake_lock_best_effort(self):
        """Android must not suspend every agent in the shared Termux UID."""
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(
            self.service.os.environ,
            {"PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "0.118"},
            clear=False,
        ), mock.patch.object(
            self.service.shutil, "which", return_value="/data/data/com.termux/files/usr/bin/termux-wake-lock"
        ), mock.patch.object(
            self.service.subprocess, "run", return_value=completed
        ) as run:
            acquired, message = self.service.ensure_termux_wake_lock()
        self.assertTrue(acquired)
        self.assertIn("wake lock active", message.lower())
        run.assert_called_once_with(
            ["/data/data/com.termux/files/usr/bin/termux-wake-lock"],
            capture_output=True,
            text=True,
            timeout=8,
        )

    def test_termux_wake_lock_failure_warns_but_never_blocks_gateway(self):
        with mock.patch.dict(
            self.service.os.environ,
            {"PREFIX": "/data/data/com.termux/files/usr", "TERMUX_VERSION": "0.118"},
            clear=False,
        ), mock.patch.object(self.service.shutil, "which", return_value=None):
            acquired, message = self.service.ensure_termux_wake_lock()
        self.assertFalse(acquired)
        self.assertIn("may suspend", message.lower())
        self.assertIn("battery", message.lower())

    def test_non_termux_platform_never_calls_wake_lock_command(self):
        env = dict(self.service.os.environ)
        env.pop("TERMUX_VERSION", None)
        env["PREFIX"] = "/usr"
        with mock.patch.dict(self.service.os.environ, env, clear=True), \
             mock.patch.object(self.service.subprocess, "run") as run:
            acquired, message = self.service.ensure_termux_wake_lock()
        self.assertFalse(acquired)
        self.assertEqual(message, "")
        run.assert_not_called()

    @unittest.skipIf(os.name == "nt", "start_new_session is POSIX-only; Windows spawn is covered in test_windows_support")
    def test_start_records_pid_and_invokes_cli_gateway_run(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        cfg["gateways"]["webhook"].update({"enabled": True, "token": "test-webhook-token-long-enough"})
        self.config.save_config(cfg)
        process = mock.Mock(pid=43210)
        with mock.patch.object(self.service.subprocess, "Popen", return_value=process) as popen, mock.patch.object(self.service, "_process_start_ticks", return_value="777"):
            started, message = self.service.start(only=["telegram", "webhook"])
        self.assertTrue(started)
        self.assertIn("43210", message)
        command = popen.call_args.args[0]
        self.assertEqual(command[:4], [sys.executable, "-m", "zeline.cli", "gateway"])
        self.assertIn("run", command)
        self.assertEqual(command.count("--only"), 2)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        state = json.loads(self.config.PID_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["pid"], 43210)
        self.assertEqual(state["start_ticks"], "777")
        self.assertEqual(state["only"], ["telegram", "webhook"])

    def test_wait_until_connected_ignores_fatal_lines_from_previous_start(self):
        """Baris fatal dari start LAMA tidak boleh membuat start baru dianggap gagal.

        Bug: wait_until_connected membaca SELURUH gateway.log, jadi satu
        "token could not be verified" dari percobaan sebelumnya terus terbaca
        dan CLI melaporkan ⚠️ walaupun proses baru terhubung normal.
        """
        self.config.ensure_data_dirs()
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        stale = "  [telegram] token could not be verified; gateway stopped.\n"
        self.service.LOG_FILE.write_text(stale, encoding="utf-8")
        # Ukuran byte nyata, bukan len(str) — Windows menulis CRLF.
        offset = self.service.LOG_FILE.stat().st_size
        self.config.PID_FILE.write_text(
            json.dumps({"pid": 4242, "start_ticks": "1", "only": ["telegram"], "log_offset": offset}),
            encoding="utf-8",
        )
        with self.service.LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write("  [telegram] @zerolinearbot connected via polling\n")
        with mock.patch.object(self.service, "_process_matches_state", return_value=True):
            ready, lines = self.service.wait_until_connected(timeout=2.0)
        self.assertTrue(ready, msg=f"expected connected, got {lines}")
        self.assertEqual(lines, ["telegram: connected"])

    def test_wait_until_connected_reports_fatal_from_current_start(self):
        """Kegagalan token dari start SEKARANG tetap harus dilaporkan."""
        self.config.ensure_data_dirs()
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        self.service.LOG_FILE.write_text("", encoding="utf-8")
        self.config.PID_FILE.write_text(
            json.dumps({"pid": 4242, "start_ticks": "1", "only": ["telegram"], "log_offset": 0}),
            encoding="utf-8",
        )
        with self.service.LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write("  [telegram] token could not be verified (rejected by Telegram: Unauthorized); gateway stopped.\n")
        with mock.patch.object(self.service, "_process_matches_state", return_value=True):
            ready, lines = self.service.wait_until_connected(timeout=2.0)
        self.assertFalse(ready)
        self.assertTrue(any("could not be verified" in line for line in lines))

    def test_start_records_log_offset_for_readiness_watch(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        self.config.ensure_data_dirs()
        self.service.LOG_FILE.write_text("old line\n", encoding="utf-8")
        # Bandingkan dengan ukuran BYTE di disk, bukan panjang string: di Windows
        # "\n" ditulis sebagai CRLF, jadi len(str) meleset satu byte per baris.
        expected_offset = self.service.LOG_FILE.stat().st_size
        process = mock.Mock(pid=43211)
        with mock.patch.object(self.service.subprocess, "Popen", return_value=process), mock.patch.object(self.service, "_process_start_ticks", return_value="777"):
            started, _message = self.service.start(only=["telegram"])
        self.assertTrue(started)
        state = json.loads(self.config.PID_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["log_offset"], expected_offset)

    def test_stop_refuses_pid_reuse_without_signalling_process(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 999, "start_ticks": "old", "only": []}), encoding="utf-8")
        with mock.patch.object(self.service, "_pid_alive", return_value=True), mock.patch.object(self.service, "_process_start_ticks", return_value="new"), mock.patch.object(self.service.os, "kill") as kill:
            stopped, message = self.service.stop(wait_seconds=0)
        self.assertFalse(stopped)
        self.assertIn("not a matching zeline process", message.lower())
        kill.assert_not_called()
        self.assertFalse(self.config.PID_FILE.exists())

    def test_start_refuses_when_existing_pid_is_alive(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 44, "start_ticks": "valid", "only": []}), encoding="utf-8")
        with mock.patch.object(self.service, "_process_matches_state", return_value=True), mock.patch.object(self.service.subprocess, "Popen") as popen:
            started, message = self.service.start()
        self.assertFalse(started)
        self.assertIn("already running", message.lower())
        popen.assert_not_called()

    def test_start_refuses_invalid_enabled_gateway_before_spawning(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"]["enabled"] = True  # token masih kosong → invalid
        self.config.save_config(cfg)
        with mock.patch.object(self.service.subprocess, "Popen") as popen:
            started, message = self.service.start()
        self.assertFalse(started)
        self.assertIn("telegram token is empty", message.lower())
        popen.assert_not_called()

    def test_status_cleans_stale_pid_state(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 98765, "only": []}), encoding="utf-8")
        with mock.patch.object(self.service, "_pid_alive", return_value=False):
            active, message, state = self.service.status()
        self.assertFalse(active)
        self.assertIn("not running", message.lower())
        self.assertIsNone(state)
        self.assertFalse(self.config.PID_FILE.exists())

    @unittest.skipIf(os.name == "nt", "POSIX process groups (killpg/getpgid) do not exist on Windows; see test_windows_support")
    def test_stop_sends_sigterm_and_removes_state(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 12345, "start_ticks": "valid", "only": []}), encoding="utf-8")
        # guard True, then grace-loop sees process gone → graceful stop, no SIGKILL.
        with mock.patch.object(self.service, "_process_matches_state", side_effect=[True, False]), mock.patch.object(self.service.os, "getpgid", return_value=12345), mock.patch.object(self.service.os, "killpg") as killpg:
            stopped, message = self.service.stop(wait_seconds=5, grace_seconds=5)
        self.assertTrue(stopped)
        killpg.assert_called_once_with(12345, signal.SIGTERM)
        self.assertIn("stopped", message.lower())
        self.assertNotIn("sigkill", message.lower())
        self.assertFalse(self.config.PID_FILE.exists())

    @unittest.skipIf(os.name == "nt", "POSIX process groups (killpg/getpgid) do not exist on Windows; see test_windows_support")
    def test_stop_escalates_to_sigkill_when_sigterm_ignored(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 12345, "start_ticks": "valid", "only": []}), encoding="utf-8")
        # matches: initial guard True, grace-loop skipped (grace=0), post-SIGKILL check False.
        with mock.patch.object(self.service, "_process_matches_state", side_effect=[True, False]), mock.patch.object(self.service.os, "getpgid", return_value=12345), mock.patch.object(self.service.os, "killpg") as killpg:
            stopped, message = self.service.stop(wait_seconds=1, grace_seconds=0)
        self.assertTrue(stopped)
        signals = [call.args[1] for call in killpg.call_args_list]
        self.assertIn(signal.SIGTERM, signals)
        self.assertIn(signal.SIGKILL, signals)
        self.assertIn("sigkill", message.lower())
        self.assertFalse(self.config.PID_FILE.exists())

    @unittest.skipIf(os.name == "nt", "POSIX process groups (killpg/getpgid) do not exist on Windows; see test_windows_support")
    def test_stop_falls_back_to_os_kill_without_process_group(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 12345, "start_ticks": "valid", "only": []}), encoding="utf-8")
        with mock.patch.object(self.service, "_process_matches_state", side_effect=[True, False]), mock.patch.object(self.service.os, "getpgid", side_effect=ProcessLookupError), mock.patch.object(self.service.os, "kill") as kill:
            stopped, message = self.service.stop(wait_seconds=5, grace_seconds=5)
        self.assertTrue(stopped)
        kill.assert_called_once_with(12345, signal.SIGTERM)
        self.assertIn("stopped", message.lower())

    @unittest.skipIf(os.name == "nt", "POSIX process groups do not exist on Windows")
    def test_stop_never_signals_a_shared_parent_process_group(self):
        """A foreground run may share a group with its Termux shell/other agents."""
        with mock.patch.object(self.service.os, "getpgid", return_value=777), \
             mock.patch.object(self.service.os, "killpg") as killpg, \
             mock.patch.object(self.service.os, "kill") as kill:
            self.assertTrue(self.service._signal_process(12345, signal.SIGTERM))
        killpg.assert_not_called()
        kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_log_tail_is_empty_without_log_file(self):
        self.assertEqual(self.service.tail_log(), "(no gateway log yet)")

    @unittest.skipIf(os.name == "nt", "Windows resolves identity via GetProcessTimes, not ps lstart")
    def test_start_token_falls_back_to_ps_lstart_on_macos(self):
        # Simulasikan macOS/BSD: /proc tidak ada (starttime None) → pakai ps lstart.
        with mock.patch.object(self.service, "_process_start_ticks", return_value=None), \
             mock.patch.object(self.service, "_ps_field", return_value="Wed Aug 12 05:00:00 2026"):
            token = self.service._process_start_token(4321)
        self.assertEqual(token, "lstart:Wed Aug 12 05:00:00 2026")

    def test_matches_state_uses_token_and_survives_missing_proc(self):
        # State baru berbasis start_token harus cocok lewat _process_start_token
        # meski /proc tidak tersedia (macOS), asalkan token identik.
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 555, "start_token": "lstart:X", "only": []}), encoding="utf-8")
        state = self.service._load_state()
        with mock.patch.object(self.service, "_pid_alive", return_value=True), \
             mock.patch.object(self.service, "_process_start_token", return_value="lstart:X"):
            self.assertTrue(self.service._process_matches_state(state))
        # Token beda → bukan process yang sama (PID reuse), fail-closed.
        with mock.patch.object(self.service, "_pid_alive", return_value=True), \
             mock.patch.object(self.service, "_process_start_token", return_value="lstart:Y"):
            self.assertFalse(self.service._process_matches_state(state))

    def test_matches_state_without_identity_verifies_via_cmdline(self):
        # State lama tanpa token/ticks → verifikasi lewat command line (harus
        # mengandung 'zeline') agar gateway tetap bisa dihentikan.
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 606, "only": []}), encoding="utf-8")
        state = self.service._load_state()
        with mock.patch.object(self.service, "_pid_alive", return_value=True), \
             mock.patch.object(self.service, "_process_looks_like_zeline", return_value=True):
            self.assertTrue(self.service._process_matches_state(state))
        with mock.patch.object(self.service, "_pid_alive", return_value=True), \
             mock.patch.object(self.service, "_process_looks_like_zeline", return_value=False):
            self.assertFalse(self.service._process_matches_state(state))

    def test_start_still_records_pid_when_identity_unavailable(self):
        # Bila /proc DAN ps sama-sama gagal (token None), start tidak boleh
        # membunuh child yang baru sukses — cukup simpan PID tanpa token.
        cfg = self.config.config_copy()
        cfg["gateways"]["webhook"].update({"enabled": True, "token": "test-webhook-token-long-enough"})
        self.config.save_config(cfg)
        process = mock.Mock(pid=51515)
        with mock.patch.object(self.service.subprocess, "Popen", return_value=process), \
             mock.patch.object(self.service, "_process_start_token", return_value=None), \
             mock.patch.object(self.service, "_process_start_ticks", return_value=None), \
             mock.patch.object(self.service.os, "kill") as kill:
            started, message = self.service.start(only=["webhook"])
        self.assertTrue(started)
        self.assertIn("51515", message)
        kill.assert_not_called()
        state = json.loads(self.config.PID_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["pid"], 51515)

    # --- Kunci proses: penjaga terhadap gateway ganda (jawaban dobel) ---

    def test_lock_is_exclusive_and_reports_holder(self):
        # Kunci pertama dapat; percobaan kedua HARUS gagal selama yang pertama
        # masih memegang. Ini inti pencegahan dua poller pada satu token.
        #
        # Rilis di finally (bukan addCleanup) supaya fd sudah tertutup SEBELUM
        # tearDown menghapus temp dir — di Windows fd terbuka membuat
        # TemporaryDirectory gagal menghapus gateway.lock (WinError 32) dan satu
        # assert gagal jadi dua laporan.
        first = self.service.GatewayLock()
        second = self.service.GatewayLock()
        try:
            self.assertTrue(first.acquire())
            self.assertEqual(self.service.lock_holder_pid(), os.getpid())
            self.assertFalse(second.acquire())
            first.release()
            # Setelah dilepas, kunci harus bebas lagi (tidak ada stale lock).
            self.assertEqual(self.service.lock_holder_pid(), 0)
            self.assertTrue(second.acquire())
        finally:
            first.release()
            second.release()

    def test_lock_pid_region_is_readable_and_not_corrupted_by_shorter_pid(self):
        # PID ditulis di luar byte yang dikunci (Windows mengunci mandatory, jadi
        # byte terkunci tidak bisa dibaca handle lain) dan dipad lebar tetap
        # supaya PID pendek tidak menyisakan digit dari PID panjang sebelumnya.
        #
        # Rilis WAJIB di finally, bukan addCleanup: cleanup berjalan SETELAH
        # tearDown, jadi fd yang masih terbuka membuat TemporaryDirectory gagal
        # menghapus gateway.lock di Windows (WinError 32).
        lock = self.service.GatewayLock()
        try:
            with mock.patch.object(self.service.os, "getpid", return_value=123456):
                self.assertTrue(lock.acquire())
                self.assertEqual(self.service.lock_holder_pid(), 123456)
        finally:
            lock.release()
        again = self.service.GatewayLock()
        try:
            with mock.patch.object(self.service.os, "getpid", return_value=999):
                self.assertTrue(again.acquire())
                self.assertEqual(self.service.lock_holder_pid(), 999)
        finally:
            again.release()

    def test_start_refuses_when_another_process_holds_lock(self):
        # Skenario nyata: `zeline gateway run` foreground di tab lain tidak
        # menulis PID file, jadi status() bilang "tidak jalan". Tanpa cek kunci,
        # start() spawn poller KEDUA → tiap pesan dijawab dua kali.
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        held = self.service.GatewayLock()
        self.assertTrue(held.acquire())
        try:
            with mock.patch.object(self.service.subprocess, "Popen") as popen:
                started, message = self.service.start(only=["telegram"])
        finally:
            held.release()
        self.assertFalse(started)
        popen.assert_not_called()  # tidak ada proses kedua yang di-spawn
        self.assertIn("already polling", message)
        self.assertFalse(self.config.PID_FILE.exists())

    def test_stop_kills_unmanaged_poller_found_via_lock(self):
        # `gateway run` tidak punya PID file. Dulu `stop` menjawab "not running"
        # dan meninggalkannya hidup — lalu `start` menambah poller kedua.
        self.config.ensure_data_dirs()
        self.service.LOCK_FILE.write_text("31337\n", encoding="utf-8")
        with mock.patch.object(self.service, "lock_holder_pid", return_value=31337), \
             mock.patch.object(self.service, "_process_looks_like_zeline", return_value=True), \
             mock.patch.object(self.service, "_signal_process", return_value=True) as signaled, \
             mock.patch.object(self.service, "_pid_alive", return_value=False):
            stopped, message = self.service.stop(wait_seconds=1.0, grace_seconds=0.2)
        self.assertTrue(stopped)
        self.assertIn("31337", message)
        self.assertIn("unmanaged", message)
        self.assertEqual(signaled.call_args.args[0], 31337)

    def test_stop_does_not_kill_foreign_lock_holder(self):
        # Kalau pemegang kunci BUKAN proses Zeline, jangan bunuh apa pun —
        # membunuh PID asing jauh lebih berbahaya daripada gagal stop.
        self.config.ensure_data_dirs()
        with mock.patch.object(self.service, "lock_holder_pid", return_value=999), \
             mock.patch.object(self.service, "_process_looks_like_zeline", return_value=False), \
             mock.patch.object(self.service, "_signal_process") as signaled:
            stopped, message = self.service.stop(wait_seconds=1.0, grace_seconds=0.2)
        self.assertFalse(stopped)
        signaled.assert_not_called()
        self.assertIn("not look like a Zeline gateway", message)

    def test_stop_sweeps_second_poller_after_managed_stop(self):
        # Proses terkelola mati, tapi masih ada poller lain memegang kunci
        # (sisa dari sebelum kunci ada). `stop` harus menyapunya juga, kalau
        # tidak `start` berikutnya kembali menghasilkan jawaban dobel.
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(
            json.dumps({"pid": 4242, "start_ticks": "1", "only": []}), encoding="utf-8"
        )
        killed: list[int] = []

        def fake_signal(pid, _sig):
            killed.append(pid)
            return True

        with mock.patch.object(self.service, "_process_matches_state", side_effect=[True, False]), \
             mock.patch.object(self.service, "lock_holder_pid", return_value=7777), \
             mock.patch.object(self.service, "_process_looks_like_zeline", return_value=True), \
             mock.patch.object(self.service, "_pid_alive", return_value=False), \
             mock.patch.object(self.service, "_signal_process", side_effect=fake_signal):
            stopped, message = self.service.stop(wait_seconds=1.0, grace_seconds=0.2)
        self.assertTrue(stopped)
        self.assertIn(4242, killed)
        self.assertIn(7777, killed)
        self.assertIn("second poller", message)
        self.assertFalse(self.config.PID_FILE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
