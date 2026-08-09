"""Tests lifecycle service gateway background Aesora."""
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
    os.environ["AESORA_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "aesora" or module_name.startswith("aesora."):
            sys.modules.pop(module_name, None)
    cfg = importlib.import_module("aesora.config")
    service = importlib.import_module("aesora.gateway_service")
    return cfg, service


class GatewayServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("AESORA_HOME")
        self.home = Path(self.temp.name) / "aesora-home"
        self.config, self.service = fresh_service(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("AESORA_HOME", None)
        else:
            os.environ["AESORA_HOME"] = self.old_home
        self.temp.cleanup()

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
        self.assertEqual(command[:4], [sys.executable, "-m", "aesora.cli", "gateway"])
        self.assertIn("run", command)
        self.assertEqual(command.count("--only"), 2)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        state = json.loads(self.config.PID_FILE.read_text())
        self.assertEqual(state["pid"], 43210)
        self.assertEqual(state["start_ticks"], "777")
        self.assertEqual(state["only"], ["telegram", "webhook"])

    def test_stop_refuses_pid_reuse_without_signalling_process(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 999, "start_ticks": "old", "only": []}))
        with mock.patch.object(self.service, "_pid_alive", return_value=True), mock.patch.object(self.service, "_process_start_ticks", return_value="new"), mock.patch.object(self.service.os, "kill") as kill:
            stopped, message = self.service.stop(wait_seconds=0)
        self.assertFalse(stopped)
        self.assertIn("bukan process zeline", message.lower())
        kill.assert_not_called()
        self.assertFalse(self.config.PID_FILE.exists())

    def test_start_refuses_when_existing_pid_is_alive(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 44, "start_ticks": "valid", "only": []}))
        with mock.patch.object(self.service, "_process_matches_state", return_value=True), mock.patch.object(self.service.subprocess, "Popen") as popen:
            started, message = self.service.start()
        self.assertFalse(started)
        self.assertIn("sudah berjalan", message.lower())
        popen.assert_not_called()

    def test_start_refuses_invalid_enabled_gateway_before_spawning(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"]["enabled"] = True  # token masih kosong → invalid
        self.config.save_config(cfg)
        with mock.patch.object(self.service.subprocess, "Popen") as popen:
            started, message = self.service.start()
        self.assertFalse(started)
        self.assertIn("token telegram kosong", message.lower())
        popen.assert_not_called()

    def test_status_cleans_stale_pid_state(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 98765, "only": []}))
        with mock.patch.object(self.service, "_pid_alive", return_value=False):
            active, message, state = self.service.status()
        self.assertFalse(active)
        self.assertIn("tidak berjalan", message.lower())
        self.assertIsNone(state)
        self.assertFalse(self.config.PID_FILE.exists())

    def test_stop_sends_sigterm_and_removes_state(self):
        self.config.ensure_data_dirs()
        self.config.PID_FILE.write_text(json.dumps({"pid": 12345, "start_ticks": "valid", "only": []}))
        with mock.patch.object(self.service, "_process_matches_state", side_effect=[True, False]), mock.patch.object(self.service.os, "kill") as kill:
            stopped, message = self.service.stop(wait_seconds=0)
        self.assertTrue(stopped)
        kill.assert_called_once_with(12345, signal.SIGTERM)
        self.assertIn("dihentikan", message.lower())
        self.assertFalse(self.config.PID_FILE.exists())

    def test_log_tail_is_empty_without_log_file(self):
        self.assertEqual(self.service.tail_log(), "(belum ada log gateway)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
