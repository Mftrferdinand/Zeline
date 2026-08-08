"""CLI regression tests untuk alur install/operasi mirip Hermes."""
from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_cli(home: Path):
    os.environ["AESORA_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "aesora" or module_name.startswith("aesora."):
            sys.modules.pop(module_name, None)
    config = importlib.import_module("aesora.config")
    cli = importlib.import_module("aesora.cli")
    return config, cli


class AesoraCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("AESORA_HOME")
        self.config, self.cli = fresh_cli(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("AESORA_HOME", None)
        else:
            os.environ["AESORA_HOME"] = self.old_home
        self.temp.cleanup()

    def invoke(self, args: list[str], expected_status: int = 0) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = self.cli.main(args)
        self.assertEqual(status, expected_status)
        return output.getvalue()

    def test_config_path_creates_no_secret_and_reports_user_config_location(self):
        result = self.invoke(["config", "path"])
        self.assertIn(str(self.home / "config.json"), result)
        self.assertFalse((self.home / "config.json").exists())

    def test_gateway_enable_webhook_generates_secret_and_uses_loopback_default(self):
        result = self.invoke(["gateway", "enable", "webhook"])
        self.assertIn("webhook diaktifkan", result.lower())
        saved = __import__("json").loads((self.home / "config.json").read_text())
        webhook = saved["gateways"]["webhook"]
        self.assertTrue(webhook["enabled"])
        self.assertEqual(webhook["host"], "127.0.0.1")
        self.assertGreaterEqual(len(webhook["token"]), 16)
        self.assertNotIn(webhook["token"], result)  # command biasa tidak bocorkan secret

    def test_gateway_list_shows_configuration_and_no_plain_token(self):
        self.invoke(["gateway", "enable", "webhook"])
        result = self.invoke(["gateway", "list"])
        self.assertIn("webhook", result)
        self.assertIn("AKTIF", result)
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertNotIn(saved["gateways"]["webhook"]["token"], result)

    def test_gateway_disable_is_persistent(self):
        self.invoke(["gateway", "enable", "webhook"])
        self.invoke(["gateway", "disable", "webhook"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertFalse(saved["gateways"]["webhook"]["enabled"])

    def test_gateway_token_requires_explicit_command(self):
        self.invoke(["gateway", "enable", "webhook"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        token = saved["gateways"]["webhook"]["token"]
        normal = self.invoke(["gateway", "list"])
        revealed = self.invoke(["gateway", "token", "webhook"])
        self.assertNotIn(token, normal)
        self.assertIn(token, revealed)
        self.assertIn("rahasia", revealed.lower())

    def test_gateway_start_refuses_when_no_enabled_gateway(self):
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        self.config.save_config(cfg)
        result = self.invoke(["gateway", "start"], expected_status=2)
        self.assertIn("tidak ada gateway aktif", result.lower())

    def test_setup_secret_uses_hidden_prompt(self):
        with mock.patch("builtins.input", side_effect=["Aesora", "https://api.example/v1", "demo-model", "n", "n", "n"]), mock.patch.object(self.cli.getpass, "getpass", return_value="hidden-api-key") as hidden:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = self.cli.main(["setup"])
        self.assertEqual(status, 0)
        hidden.assert_called_once()
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertEqual(saved["provider"]["api_key"], "hidden-api-key")
        self.assertNotIn("hidden-api-key", output.getvalue())

    def test_setup_from_hermes_requires_explicit_flag_and_migrates_provider(self):
        with mock.patch.object(self.config, "hermes_provider", return_value={
            "base_url": "http://localhost:20128/v1",
            "api_key": "test-migration-key",
            "model": "Vibe/test-model",
        }):
            # Setup asks: name, base URL, API key, model, lalu 3 pilihan gateway.
            with mock.patch("builtins.input", side_effect=["", "", "", "n", "n", "n"]), mock.patch.object(self.cli.getpass, "getpass", return_value=""):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = self.cli.main(["setup", "--from-hermes"])
        self.assertEqual(status, 0)
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertEqual(saved["provider"]["base_url"], "http://localhost:20128/v1")
        self.assertEqual(saved["provider"]["api_key"], "test-migration-key")
        self.assertIn("migrasi", output.getvalue().lower())

    def test_doctor_reports_missing_provider_key_without_crashing(self):
        result = self.invoke(["doctor"], expected_status=1)
        self.assertIn("api key", result.lower())
        self.assertIn("aesora setup", result.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
