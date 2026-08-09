"""CLI regression tests for installation and operation flows."""
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

    def test_gateway_token_command_masks_stored_token(self):
        self.invoke(["gateway", "enable", "webhook"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        token = saved["gateways"]["webhook"]["token"]
        normal = self.invoke(["gateway", "list"])
        masked = self.invoke(["gateway", "token", "webhook"])
        self.assertNotIn(token, normal)
        self.assertNotIn(token, masked)
        self.assertIn("**", masked)

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

    def test_secret_prompt_explains_hidden_input_and_confirms_capture(self):
        with mock.patch.object(self.cli.getpass, "getpass", return_value="secret-value") as hidden:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                value = self.cli._ask("Telegram bot token", secret=True)
        self.assertEqual(value, "secret-value")
        self.assertIn("input hidden", hidden.call_args.args[0].lower())
        self.assertIn("saved securely", output.getvalue().lower())
        self.assertNotIn("secret-value", output.getvalue())

    def test_model_command_updates_provider_and_model_without_reconfiguring_gateways(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:telegram-token"})
        self.config.save_config(cfg)
        with mock.patch("builtins.input", side_effect=["https://api.example/v1", "research-model"]), mock.patch.object(self.cli.getpass, "getpass", return_value="provider-secret"):
            result = self.invoke(["model"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertIn("Model disimpan", result)
        self.assertEqual(saved["provider"], {
            "base_url": "https://api.example/v1",
            "api_key": "provider-secret",
            "model": "research-model",
        })
        self.assertEqual(saved["gateways"]["telegram"]["token"], "123:telegram-token")

    def test_gateway_restart_stops_then_starts_with_same_selection(self):
        with mock.patch.object(self.cli.gateway_service, "stop", return_value=(True, "stopped")) as stop, mock.patch.object(self.cli.gateway_service, "start", return_value=(True, "started")) as start:
            result = self.invoke(["gateway", "restart", "--only", "telegram"])
        stop.assert_called_once()
        start.assert_called_once_with(only=["telegram"])
        self.assertIn("started", result)

    def test_zeline_wordmark_and_product_subtitle_are_precise_in_plain_mode(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.cli._print_banner()
        lines = output.getvalue().splitlines()
        subtitle = next(line for line in lines if "ZELINE" in line)
        wordmark = lines[1:5]
        self.assertEqual(wordmark, [
            " _______ ___  ___  _    ___ _  _ ___   _   ___ ",
            "|_  / __| _ \\/ _ \\| |  |_ _| \\| | __| /_\\ | _ \\",
            " / /| _||   / (_) | |__ | || .` | _| / _ \\|   /",
            "/___|___|_|_\\\\___/|____|___|_|\\_|___/_/ \\_\\_|_\\",
        ])
        self.assertIn("ZELINE AGENTIC AI · v0.1.0 · BY MFTRFERDINAND", subtitle)
        self.assertIn("BY MFTRFERDINAND", subtitle)
        self.assertNotIn("┏", output.getvalue())
        self.assertNotIn("AESORA", output.getvalue().upper())

    def test_setup_replaces_stale_provider_defaults_when_no_key_exists(self):
        cfg = self.config.config_copy()
        cfg["provider"].update({
            "base_url": "http://localhost:20128/v1",
            "api_key": "",
            "model": "Vibe/ds/deepseek-v4-pro",
        })
        self.config.save_config(cfg)
        with mock.patch("builtins.input", side_effect=["", "", "gpt-4o-mini", "n", "n", "n"]), mock.patch.object(self.cli.getpass, "getpass", return_value="new-key"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = self.cli.main(["setup"])
        self.assertEqual(status, 0)
        self.assertIn("STEP 1/3", output.getvalue())
        self.assertIn("Nothing is imported automatically", output.getvalue())
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertEqual(saved["provider"]["base_url"], "https://api.openai.com/v1")
        self.assertEqual(saved["provider"]["model"], "gpt-4o-mini")

    def test_setup_reset_starts_with_generic_provider_defaults(self):
        cfg = self.config.config_copy()
        cfg["provider"].update({"base_url": "http://example.invalid/v1", "api_key": "old-key", "model": "old-model"})
        self.config.save_config(cfg)
        with mock.patch("builtins.input", side_effect=["", "", "gpt-4o-mini", "n", "n", "n"]), mock.patch.object(self.cli.getpass, "getpass", return_value=""):
            self.assertEqual(self.cli.main(["setup", "--reset"]), 0)
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertEqual(saved["provider"]["base_url"], "https://api.openai.com/v1")
        self.assertEqual(saved["provider"]["api_key"], "")

    def test_top_level_gateway_aliases_dispatch_to_gateway_commands(self):
        with mock.patch.object(self.cli, "cmd_gateway_start", return_value=0) as start:
            self.assertEqual(self.cli.main(["start"]), 0)
        start.assert_called_once_with(None)


    def test_doctor_reports_missing_provider_key_without_crashing(self):
        result = self.invoke(["doctor"], expected_status=1)
        self.assertIn("api key", result.lower())
        self.assertIn("zeline setup", result.lower())

    def test_cli_identity_uses_zeline_command_and_large_zerolinear_wordmark(self):
        parser = self.cli.build_parser()
        self.assertEqual(parser.prog, "zeline")
        result = self.invoke(["status"], expected_status=1)
        self.assertIn(" _______ ___  ___  _    ___ _  _ ___   _   ___ ", result)
        self.assertIn("ZELINE AGENTIC AI · v0.1.0 · BY MFTRFERDINAND", result)
        self.assertIn("BY MFTRFERDINAND", result)
        self.assertNotIn("AESORA", result.upper())

    def test_banner_falls_back_to_plain_text_when_color_is_disabled(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.cli._print_banner()
        self.assertIn(" _______ ___  ___  _    ___ _  _ ___   _   ___ ", output.getvalue())
        self.assertIn("ZELINE", output.getvalue())
        self.assertNotIn("\x1b[", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
