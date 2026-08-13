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
    os.environ["ZELINE_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "zeline" or module_name.startswith("zeline."):
            sys.modules.pop(module_name, None)
    config = importlib.import_module("zeline.config")
    cli = importlib.import_module("zeline.cli")
    return config, cli


class ZelineCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("ZELINE_HOME")
        self.config, self.cli = fresh_cli(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
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
        self.assertIn("webhook enabled", result.lower())
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
        # Config state (dihidupkan) harus jelas terpisah dari status proses.
        self.assertIn("enabled", result)
        self.assertIn("Background process", result)
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
        self.assertIn("no enabled gateway", result.lower())

    def test_gateway_picker_moves_with_arrows_and_selects_one_option(self):
        with mock.patch.object(self.cli.sys.stdin, "isatty", return_value=True), mock.patch.object(self.cli.sys.stdin, "fileno", return_value=0), mock.patch.object(self.cli.termios, "tcgetattr", return_value=[0]), mock.patch.object(self.cli.termios, "tcsetattr"), mock.patch.object(self.cli.tty, "setraw"), mock.patch.object(self.cli, "_read_menu_key", side_effect=["down", "enter"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                selected = self.cli._select_gateway()
        self.assertEqual(selected, "whatsapp")
        self.assertIn("Telegram", output.getvalue())
        self.assertIn("WhatsApp", output.getvalue())
        self.assertIn("Webhook", output.getvalue())
        self.assertIn("Cancel", output.getvalue())

    def test_setup_configures_only_selected_gateway_then_directs_model_setup(self):
        with mock.patch.object(self.cli, "_select_gateway", return_value="telegram"), mock.patch.object(self.cli, "_setup_telegram", return_value=True) as telegram, mock.patch.object(self.cli, "_setup_whatsapp") as whatsapp, mock.patch.object(self.cli, "_setup_webhook") as webhook:
            result = self.invoke(["setup"])
        telegram.assert_called_once()
        whatsapp.assert_not_called()
        webhook.assert_not_called()
        self.assertIn("zeline model", result.lower())
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertTrue(saved["gateway_setup_complete"])
        self.assertFalse(saved["setup_complete"])

    def test_bare_zeline_forces_gateway_setup_before_chat(self):
        with mock.patch.object(self.cli, "cmd_setup", return_value=0) as setup, mock.patch.object(self.cli, "cmd_chat") as chat:
            self.assertEqual(self.cli.main([]), 0)
        setup.assert_called_once_with(reset=False)
        chat.assert_not_called()

    def test_bare_zeline_directs_model_after_gateway_setup(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        cfg["setup_complete"] = False
        self.config.save_config(cfg)

        result = self.invoke([] , expected_status=2)

        self.assertIn("zeline model", result.lower())

    def test_model_setup_is_blocked_until_gateway_exists(self):
        result = self.invoke(["model"], expected_status=2)
        self.assertIn("run: zeline", result.lower())

    def test_chat_refuses_unconfirmed_legacy_provider_config(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        cfg["provider"].update({"api_key": "stale-key", "model": "stale-model"})
        cfg["setup_complete"] = False
        self.config.save_config(cfg)

        result = self.invoke(["chat", "-q", "hello"], expected_status=2)

        self.assertIn("zeline model", result.lower())

    def test_chat_refuses_model_that_was_never_verified(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        cfg["setup_complete"] = True
        cfg["provider"].update({"api_key": "key", "model": "gpt-4o-mini", "model_verified": False})
        self.config.save_config(cfg)

        result = self.invoke(["chat", "-q", "hello"], expected_status=2)

        self.assertIn("zeline model", result.lower())

    def test_secret_prompt_explains_hidden_input_and_confirms_capture(self):
        with mock.patch.object(self.cli, "_masked_secret_input", return_value="secret-value") as masked:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                value = self.cli._ask("Telegram bot token", secret=True)
        self.assertEqual(value, "secret-value")
        masked.assert_called_once()
        self.assertIn("saved securely", output.getvalue().lower())
        self.assertNotIn("secret-value", output.getvalue())

    def test_masked_secret_input_prints_one_star_per_character(self):
        output = io.StringIO()
        with mock.patch.object(self.cli.sys.stdin, "isatty", return_value=True), mock.patch.object(self.cli.sys.stdin, "fileno", return_value=0), mock.patch.object(self.cli.termios, "tcgetattr", return_value=[0]), mock.patch.object(self.cli.termios, "tcsetattr"), mock.patch.object(self.cli.tty, "setraw"), mock.patch.object(self.cli, "_read_secret_key", side_effect=["a", "b", "\x7f", "c", "\n"]), contextlib.redirect_stdout(output):
            value = self.cli._masked_secret_input("API key: ")
        self.assertEqual(value, "ac")
        self.assertIn("API key: **\b \b*", output.getvalue())
        self.assertNotIn("ac", output.getvalue())

    def test_detects_openai_models_with_bearer_auth(self):
        response = mock.Mock(ok=True)
        response.json.return_value = {"data": [{"id": "gpt-4.1"}, {"id": "gpt-4o"}]}
        with mock.patch.object(self.cli.requests, "get", return_value=response) as get:
            protocol, models = self.cli._discover_provider_models("https://api.example/v1", "secret")
        self.assertEqual(protocol, "openai")
        self.assertEqual(models, ["gpt-4.1", "gpt-4o"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

    def test_detects_anthropic_models_with_native_headers(self):
        denied = mock.Mock(ok=False)
        response = mock.Mock(ok=True)
        response.json.return_value = {"data": [{"id": "claude-sonnet-4-5"}]}
        with mock.patch.object(self.cli.requests, "get", side_effect=[denied, response]) as get:
            protocol, models = self.cli._discover_provider_models("https://api.anthropic.com/v1", "secret")
        self.assertEqual(protocol, "anthropic")
        self.assertEqual(models, ["claude-sonnet-4-5"])
        self.assertEqual(get.call_args.kwargs["headers"]["x-api-key"], "secret")

    def test_model_picker_selects_detected_model_by_number(self):
        with mock.patch("builtins.input", return_value="2"):
            selected = self.cli._choose_model(["model-a", "model-b"], "model-a")
        self.assertEqual(selected, "model-b")

    def test_configure_provider_detects_protocol_and_uses_model_picker(self):
        provider = {"base_url": "https://api.openai.com/v1", "api_key": "", "model": "old"}
        with mock.patch("builtins.input", side_effect=["https://api.example/v1", "Token Harbor", "2"]), mock.patch.object(self.cli, "_masked_secret_input", return_value="secret"), mock.patch.object(self.cli, "_discover_provider_models", return_value=("anthropic", ["claude-a", "claude-b"])):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.cli._configure_provider(provider)
        self.assertEqual(provider, {"base_url": "https://api.example/v1", "api_key": "secret", "model": "claude-b", "name": "Token Harbor", "protocol": "anthropic", "model_verified": True})
        self.assertIn("Anthropic", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_model_command_adds_provider_and_activates_without_touching_gateways(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:telegram-token"})
        self.config.save_config(cfg)
        # Arrow-menu fallback (non-TTY) pakai nomor. Tanpa provider tersimpan,
        # menu = [1]Add [2]Remove [3]Cancel. Pilih 1 (Add) -> isi provider.
        # Setelah add ada 1 provider: menu = [1]MyProvider [2]Add [3]Remove [4]Cancel -> 4.
        with mock.patch("builtins.input", side_effect=["1", "https://api.example/v1", "My Provider", "research-model", "4"]), mock.patch.object(self.cli.getpass, "getpass", return_value="provider-secret"):
            result = self.invoke(["model"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertIn("added", result)
        self.assertEqual(saved["provider"], {
            "protocol": "openai",
            "model_verified": True,
            "base_url": "https://api.example/v1",
            "api_key": "provider-secret",
            "model": "research-model",
            "name": "My Provider",
        })
        self.assertEqual(saved["providers"]["my-provider"], saved["provider"])
        self.assertEqual(saved["gateways"]["telegram"]["token"], "123:telegram-token")

    def test_model_command_removes_non_active_provider(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        cfg["setup_complete"] = True
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:telegram-token"})
        active = {"protocol": "openai", "model_verified": True, "base_url": "https://a.example/v1", "api_key": "k1", "model": "m1", "name": "Alpha"}
        spare = {"protocol": "openai", "model_verified": True, "base_url": "https://b.example/v1", "api_key": "k2", "model": "m2", "name": "Beta"}
        cfg["provider"] = dict(active)
        cfg["providers"] = {"alpha": dict(active), "beta": dict(spare)}
        self.config.save_config(cfg)
        # Menu = [1]Add url provider [2]Remove provider [3]View provider [4]Cancel.
        # 2 (Remove) -> 2 (Beta, non-aktif). Setelah hapus balik ke menu -> 4 (Cancel).
        with mock.patch("builtins.input", side_effect=["2", "2", "4"]):
            result = self.invoke(["model"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertIn("removed", result)
        self.assertNotIn("beta", saved["providers"])
        self.assertIn("alpha", saved["providers"])
        self.assertEqual(saved["provider"]["name"], "Alpha")

    def test_model_command_refuses_to_remove_active_provider(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        cfg["setup_complete"] = True
        active = {"protocol": "openai", "model_verified": True, "base_url": "https://a.example/v1", "api_key": "k1", "model": "m1", "name": "Alpha"}
        spare = {"protocol": "openai", "model_verified": True, "base_url": "https://b.example/v1", "api_key": "k2", "model": "m2", "name": "Beta"}
        cfg["provider"] = dict(active)
        cfg["providers"] = {"alpha": dict(active), "beta": dict(spare)}
        self.config.save_config(cfg)
        # Menu = [1]Add url provider [2]Remove provider [3]View provider [4]Cancel.
        # 2 (Remove) -> 1 (Alpha, aktif) -> ditolak -> balik ke menu -> 4 (Cancel).
        with mock.patch("builtins.input", side_effect=["2", "1", "4"]):
            result = self.invoke(["model"])
        saved = __import__("json").loads((self.home / "config.json").read_text())
        self.assertIn("active", result.lower())
        self.assertIn("alpha", saved["providers"])
        self.assertIn("beta", saved["providers"])

    def test_gateway_restart_stops_then_starts_with_same_selection(self):
        with mock.patch.object(self.cli.gateway_service, "stop", return_value=(True, "stopped")) as stop, mock.patch.object(self.cli.gateway_service, "start", return_value=(True, "started")) as start:
            result = self.invoke(["gateway", "restart", "--only", "telegram"])
        stop.assert_called_once()
        start.assert_called_once_with(only=["telegram"])
        self.assertIn("started", result)

    def test_gateway_run_refuses_duplicate_managed_process(self):
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        with mock.patch.object(
            self.cli.gateway_service,
            "status",
            return_value=(True, "Gateway running (PID 43210).", {"pid": 43210}),
        ), mock.patch.object(self.cli.os, "getpid", return_value=99999), mock.patch.object(
            self.cli, "run_all"
        ) as run_all:
            result = self.invoke(["gateway", "run"], expected_status=1)
        self.assertIn("already running", result.lower())
        self.assertIn("gateway stop", result.lower())
        run_all.assert_not_called()

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
