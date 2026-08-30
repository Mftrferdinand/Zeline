"""CLI regression tests for installation and operation flows."""
from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# Read from pyproject rather than importing the package: the banner assertions
# must fail if the wheel version and the package version ever drift apart, and
# importing zeline would just compare it against itself.
with (SOURCE_ROOT / "pyproject.toml").open("rb") as _handle:
    PACKAGE_VERSION = tomllib.load(_handle)["project"]["version"]


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
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
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
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn(saved["gateways"]["webhook"]["token"], result)

    def test_gateway_disable_is_persistent(self):
        self.invoke(["gateway", "enable", "webhook"])
        self.invoke(["gateway", "disable", "webhook"])
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["gateways"]["webhook"]["enabled"])

    def test_gateway_token_command_masks_stored_token(self):
        self.invoke(["gateway", "enable", "webhook"])
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
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
        # raw_mode()/read_key() now live in zeline._termkey so the CLI works on
        # Windows (no termios there). isatty must be True to reach the arrow-key
        # path; raw_mode is stubbed to a no-op.
        with mock.patch.object(self.cli.sys.stdin, "isatty", return_value=True), mock.patch.object(self.cli, "raw_mode", lambda: contextlib.nullcontext()), mock.patch.object(self.cli, "_read_menu_key", side_effect=["down", "enter"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                selected = self.cli._select_gateway()
        self.assertEqual(selected, "whatsapp")
        self.assertIn("Telegram", output.getvalue())
        self.assertIn("WhatsApp", output.getvalue())
        self.assertIn("Webhook", output.getvalue())
        self.assertIn("Cancel", output.getvalue())

    def test_first_run_configures_only_selected_gateway_then_directs_model_setup(self):
        with mock.patch.object(self.cli, "_select_gateway", return_value="telegram"), mock.patch.object(self.cli, "_setup_telegram", return_value=True) as telegram, mock.patch.object(self.cli, "_setup_whatsapp") as whatsapp, mock.patch.object(self.cli, "_setup_webhook") as webhook:
            result = self.invoke([])
        telegram.assert_called_once()
        whatsapp.assert_not_called()
        webhook.assert_not_called()
        self.assertIn("zeline model", result.lower())
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["gateway_setup_complete"])
        self.assertFalse(saved["setup_complete"])

    def test_setup_command_keeps_first_run_gateway_onboarding_compatibility(self):
        with mock.patch.object(self.cli, "cmd_setup", return_value=0) as onboarding, \
             mock.patch.object(self.cli, "cmd_setup_center") as center:
            self.assertEqual(self.cli.main(["setup"]), 0)
        onboarding.assert_called_once_with(reset=False)
        center.assert_not_called()

    def test_tools_list_shows_profiles_and_every_native_tool(self):
        result = self.invoke(["tools", "list"])
        self.assertIn("safe", result.lower())
        self.assertIn("workspace", result.lower())
        self.assertIn("full", result.lower())
        self.assertIn("runtime_info", result)
        self.assertIn("run_shell", result)

    def test_tools_profile_updates_local_cli_profile(self):
        result = self.invoke(["tools", "profile", "workspace"])
        self.assertIn("workspace", result.lower())
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["tools"]["cli_profile"], "workspace")

    def test_tools_profile_can_update_a_gateway_without_exposing_full_publicly(self):
        result = self.invoke(["tools", "profile", "full", "--gateway", "telegram"], expected_status=2)
        self.assertIn("allowlist", result.lower())
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"]["allowed"] = ["111222333"]
        self.config.save_config(cfg)
        result = self.invoke(
            [
                "tools", "profile", "full", "--gateway", "telegram",
                "--owner", "111222333", "--allow-remote-code-execution",
            ]
        )
        self.assertIn("telegram", result.lower())
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["gateways"]["telegram"]["tool_profile"], "full")
        self.assertEqual(saved["gateways"]["telegram"]["owner_identity"], "111222333")

    def test_tools_full_gateway_rejects_unconfirmed_or_non_allowlisted_owner(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"]["allowed"] = ["111222333"]
        self.config.save_config(cfg)
        missing_ack = self.invoke(
            ["tools", "profile", "full", "--gateway", "telegram", "--owner", "111222333"],
            expected_status=2,
        )
        self.assertIn("remote code execution", missing_ack.lower())
        wrong_owner = self.invoke(
            [
                "tools", "profile", "full", "--gateway", "telegram",
                "--owner", "999888777", "--allow-remote-code-execution",
            ],
            expected_status=2,
        )
        self.assertIn("must exactly match", wrong_owner.lower())

    def test_tools_elevated_gateway_rejects_wildcard_allowlist(self):
        cfg = self.config.config_copy()
        cfg["gateways"]["telegram"]["allowed"] = ["*"]
        self.config.save_config(cfg)
        result = self.invoke(
            ["tools", "profile", "workspace", "--gateway", "telegram"],
            expected_status=2,
        )
        self.assertIn("exact owner allowlist", result.lower())

    def test_tools_disable_removes_tool_from_executor_and_enable_restores_it(self):
        self.invoke(["tools", "disable", "run_shell"])
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("cli:test", profile="full", workspace=self.home)
        self.assertNotIn("run_shell", {item["function"]["name"] for item in executor.schemas})
        self.invoke(["tools", "enable", "run_shell"])
        executor = tools.ToolExecutor("cli:test", profile="full", workspace=self.home)
        self.assertIn("run_shell", {item["function"]["name"] for item in executor.schemas})

    def test_tools_workspace_requires_an_existing_directory_and_persists_it(self):
        missing = self.home / "missing"
        result = self.invoke(["tools", "workspace", str(missing)], expected_status=2)
        self.assertIn("not found", result.lower())
        workspace = self.home / "workspace"
        workspace.mkdir(parents=True)
        result = self.invoke(["tools", "workspace", str(workspace)])
        reported = result.strip().removeprefix("Workspace: ")
        self.assertTrue(Path(reported).samefile(workspace))
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(Path(saved["tools"]["workspace"]).samefile(workspace))

    def test_setup_parser_exposes_zeline_style_sections(self):
        parser = self.cli.build_parser()
        for section in ("gateway", "model", "tools", "integrations", "agent"):
            with self.subTest(section=section):
                parsed = parser.parse_args(["setup", section])
                self.assertEqual(parsed.setup_section, section)

    def test_setup_section_dispatches_without_repeating_first_run_gateway_flow(self):
        with mock.patch.object(self.cli, "cmd_setup_tools", return_value=0) as tools_setup, \
             mock.patch.object(self.cli, "_select_gateway") as gateway_picker:
            self.assertEqual(self.cli.main(["setup", "tools"]), 0)
        tools_setup.assert_called_once_with()
        gateway_picker.assert_not_called()

    def test_bare_setup_opens_setup_center_and_dispatches_selected_section(self):
        cfg = self.config.config_copy()
        cfg["gateway_setup_complete"] = True
        self.config.save_config(cfg)
        with mock.patch.object(self.cli, "_arrow_menu", side_effect=[2, 5]), \
             mock.patch.object(self.cli, "cmd_setup_tools", return_value=0) as tools_setup, \
             mock.patch.object(self.cli, "cmd_setup") as first_run:
            result = self.invoke(["setup"])
        tools_setup.assert_called_once_with()
        first_run.assert_not_called()
        self.assertIn("setup center", result.lower())

    def test_agent_setup_persists_validated_runtime_preferences(self):
        answers = iter(["Zeline Agent", "12", "80", "n", "y"])
        with mock.patch.object(self.cli, "_ask", side_effect=lambda *args, **kwargs: next(answers)):
            result = self.invoke(["setup", "agent"])
        self.assertIn("agent settings saved", result.lower())
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["name"], "Zeline Agent")
        self.assertEqual(saved["agent"]["max_tool_rounds"], 12)
        self.assertEqual(saved["agent"]["max_sessions"], 80)
        self.assertFalse(saved["agent"]["stream"])
        self.assertTrue(saved["agent"]["persist_sessions"])

    def test_agent_setup_rejects_out_of_range_numbers_without_writing(self):
        with mock.patch.object(self.cli, "_ask", side_effect=["Zeline", "0", "50", "y", "y"]):
            result = self.invoke(["setup", "agent"], expected_status=2)
        self.assertIn("max tool rounds", result.lower())
        self.assertFalse((self.home / "config.json").exists())

    def test_telegram_setup_with_owner_id_enables_full_toolset_and_mcp(self):
        # An installer who names their own numeric chat ID as owner should get the
        # full toolset (native tools + MCP + image analysis) with zero extra
        # commands. The single-owner allowlist + RCE ack satisfy the gateway
        # security policy so the elevated profile actually starts.
        answers = iter(["123:abctoken", "7387183839"])
        with mock.patch.object(self.cli, "_ask", side_effect=lambda *a, **k: next(answers)):
            cfg = self.config.config_copy()
            ok = self.cli._setup_telegram(cfg)
        self.assertTrue(ok)
        tg = cfg["gateways"]["telegram"]
        self.assertEqual(tg["tool_profile"], "full")
        self.assertEqual(tg["allowed"], ["7387183839"])
        self.assertEqual(tg["owner_identity"], "7387183839")
        self.assertIs(tg["remote_code_execution_ack"], True)
        # The policy validator must accept this config unchanged.
        from zeline.gateways import validate_gateway
        self.assertEqual(validate_gateway("telegram", tg), [])

    def test_telegram_setup_without_owner_stays_safe_public(self):
        answers = iter(["123:abctoken", ""])
        with mock.patch.object(self.cli, "_ask", side_effect=lambda *a, **k: next(answers)):
            cfg = self.config.config_copy()
            ok = self.cli._setup_telegram(cfg)
        self.assertTrue(ok)
        tg = cfg["gateways"]["telegram"]
        self.assertEqual(tg["tool_profile"], "safe")
        self.assertEqual(tg["allowed"], [])
        self.assertNotIn("owner_identity", tg)
        self.assertNotIn("remote_code_execution_ack", tg)

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
        # Secret reading moved to zeline._termkey.read_secret; patch its raw_mode
        # and read_key so the test runs identically on POSIX and Windows.
        termkey = self.cli.read_secret.__module__
        import importlib

        tk = importlib.import_module(termkey)
        with mock.patch.object(tk.sys.stdin, "isatty", return_value=True), mock.patch.object(tk, "raw_mode", lambda: contextlib.nullcontext()), mock.patch.object(tk, "read_key", side_effect=["a", "b", "\x7f", "c", "\n"]), contextlib.redirect_stdout(output):
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
        self.assertEqual(provider, {"base_url": "https://api.example/v1", "api_key": "secret", "model": "claude-b", "image_model": "", "name": "Token Harbor", "protocol": "anthropic", "model_verified": True})
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
        with mock.patch("builtins.input", side_effect=["1", "https://api.example/v1", "My Provider", "research-model", "4"]), mock.patch.object(self.cli, "_masked_secret_input", return_value="provider-secret"):
            result = self.invoke(["model"])
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertIn("added", result)
        self.assertEqual(saved["provider"], {
            "protocol": "openai",
            "model_verified": True,
            "base_url": "https://api.example/v1",
            "api_key": "provider-secret",
            "model": "research-model",
            "image_model": "",
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
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
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
        saved = __import__("json").loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertIn("active", result.lower())
        self.assertIn("alpha", saved["providers"])
        self.assertIn("beta", saved["providers"])

    def test_gateway_restart_stops_then_starts_with_same_selection(self):
        with mock.patch.object(self.cli.gateway_service, "stop", return_value=(True, "stopped")) as stop, mock.patch.object(self.cli.gateway_service, "start", return_value=(True, "started")) as start, mock.patch.object(self.cli.gateway_service, "wait_until_connected", return_value=(True, ["telegram: connected"])):
            result = self.invoke(["gateway", "restart", "--only", "telegram"])
        stop.assert_called_once()
        start.assert_called_once_with(only=["telegram"])
        self.assertIn("started", result)
        self.assertIn("connected", result.lower())

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

    def test_gateway_run_refuses_when_lock_is_held_by_another_run(self):
        # Dua `zeline gateway run` (dua tab Termux) dulu sama-sama lolos karena
        # tidak ada yang menulis PID file → dua poller pada satu token → tiap
        # pesan dijawab DUA KALI. Kunci proses yang menutup celah ini.
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        held = self.cli.gateway_service.GatewayLock()
        self.assertTrue(held.acquire())
        try:
            with mock.patch.object(self.cli, "run_all") as run_all:
                result = self.invoke(["gateway", "run"], expected_status=1)
        finally:
            held.release()
        self.assertIn("already polling", result.lower())
        self.assertIn("gateway stop", result.lower())
        run_all.assert_not_called()

    def test_gateway_run_warns_termux_users_to_prefer_managed_start(self):
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        cfg["gateways"]["telegram"].update({"enabled": True, "token": "123:abc"})
        self.config.save_config(cfg)
        fake_lock = mock.Mock()
        fake_lock.acquire.return_value = True
        with mock.patch.object(self.cli.gateway_service, "status", return_value=(False, "not running", None)), \
             mock.patch.object(self.cli.gateway_service, "is_termux", return_value=True), \
             mock.patch.object(self.cli.gateway_service, "GatewayLock", return_value=fake_lock), \
             mock.patch.object(self.cli, "_run_gateway_loop", return_value=0):
            result = self.invoke(["gateway", "run"])
        self.assertIn("foreground", result.lower())
        self.assertIn("zeline gateway start", result.lower())
        fake_lock.release.assert_called_once()

    def test_gateway_list_flags_unmanaged_poller(self):
        # Poller di luar `gateway start` tidak punya PID file, jadi dulu
        # dilaporkan "not running" padahal aktif menjawab — user bingung kenapa
        # jawabannya dobel. Sekarang kunci membuatnya terlihat.
        with mock.patch.object(self.cli.gateway_service, "status", return_value=(False, "not running", None)), \
             mock.patch.object(self.cli.gateway_service, "lock_holder_pid", return_value=31337):
            result = self.invoke(["gateway", "list"])
        self.assertIn("unmanaged poller", result.lower())
        self.assertIn("31337", result)

    def test_zeline_wordmark_and_product_subtitle_are_precise_in_plain_mode(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.cli._print_banner()
        text = output.getvalue()
        # Boxed identity: title + subtitle inside one frame, no ANSI in plain mode.
        self.assertIn("Z  E  L  I  N  E", text)
        self.assertIn(f"AGENTIC AI BY ZEROLINEAR • v{PACKAGE_VERSION}", text)
        self.assertIn("╭", text)
        self.assertIn("╰", text)
        self.assertNotIn("\x1b[", text)


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
        self.assertIn("Z  E  L  I  N  E", result)
        self.assertIn(f"AGENTIC AI BY ZEROLINEAR • v{PACKAGE_VERSION}", result)
        self.assertIn("ZEROLINEAR", result)


    def test_banner_falls_back_to_plain_text_when_color_is_disabled(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.cli._print_banner()
        self.assertIn("Z  E  L  I  N  E", output.getvalue())
        self.assertIn("AGENTIC AI BY ZEROLINEAR", output.getvalue())
        self.assertNotIn("\x1b[", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
