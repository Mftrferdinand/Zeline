"""Regression tests untuk fondasi Zeline publik.

Jalankan tanpa provider/API key sungguhan:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import html
import http.client
import importlib
import json
import re
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_zeline(home: Path):
    """Reload package with a fully-isolated ZELINE_HOME."""
    os.environ["ZELINE_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "zeline" or module_name.startswith("zeline."):
            sys.modules.pop(module_name, None)
    cfg = importlib.import_module("zeline.config")
    memory = importlib.import_module("zeline.memory")
    tools = importlib.import_module("zeline.tools")
    return cfg, memory, tools


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ZelinePublicCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "zeline-home"
        self.old_zeline_home = os.environ.get("ZELINE_HOME")
        self.old_key = os.environ.pop("ZELINE_API_KEY", None)
        self.old_base = os.environ.pop("ZELINE_BASE_URL", None)
        self.old_model = os.environ.pop("ZELINE_MODEL", None)
        self.config, self.memory, self.tools = fresh_zeline(self.home)

    def tearDown(self):
        if self.old_zeline_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_zeline_home

        for key, value in (("ZELINE_API_KEY", self.old_key), ("ZELINE_BASE_URL", self.old_base), ("ZELINE_MODEL", self.old_model)):
            if value is not None:
                os.environ[key] = value
        self.temp.cleanup()

    def test_fresh_public_install_has_no_implicit_external_secret_import(self):
        """A public install cannot silently bind itself to another app's key."""
        self.assertEqual(self.config.API_KEY, "")
        self.assertEqual(self.config.BASE_URL, "https://api.openai.com/v1")
        self.assertEqual(self.config.MODEL, "gpt-4o-mini")

    def test_default_runtime_uses_zeline_identity(self):
        self.assertIn("Zeline", self.config.SYSTEM_PROMPT)
        self.assertIn("Zerolinear", self.config.SYSTEM_PROMPT)
        self.assertEqual(self.config.NAME, "Zeline")
        self.assertIn("eksekusi", self.config.SYSTEM_PROMPT.lower())

    def test_existing_zeline_config_keeps_agent_name_and_model(self):
        saved = self.config.config_copy()
        saved["name"] = "Lucian"
        saved["provider"]["model"] = "keep-this-model"
        self.config.save_config(saved)

        normalized = self.config.stored_config_copy()

        self.assertEqual(normalized["name"], "Lucian")
        self.assertEqual(normalized["provider"]["model"], "keep-this-model")

    def test_seeded_superagent_skill_corpus_is_available_to_public_gateway(self):
        skill_system = importlib.import_module("zeline.skills")
        skill_system.seed_skills()
        content = skill_system.load_skill("superagent-v7-sk0")
        self.assertIn("Skill Registry", content)

    def test_memory_isolated_between_platform_users(self):
        one = self.memory.MemoryStore("telegram:100")
        two = self.memory.MemoryStore("telegram:200")
        one.add("Suka kopi tanpa gula")
        self.assertIn("kopi", one.formatted())
        self.assertEqual(two.formatted(), "(memory kosong)")

    def test_memory_per_identity_has_bounded_fact_count(self):
        store = self.memory.MemoryStore("telegram:bounded")
        with mock.patch.object(self.memory, "MAX_FACTS_PER_IDENTITY", 2):
            self.assertIn("disimpan", store.add("fakta satu"))
            self.assertIn("disimpan", store.add("fakta dua"))
            self.assertIn("batas", store.add("fakta tiga").lower())
        self.assertEqual(store.list(), ["fakta satu", "fakta dua"])

    def test_memory_rejects_new_identity_after_global_file_limit(self):
        self.memory.MemoryStore("telegram:first").add("fakta pertama")
        with mock.patch.object(self.memory, "MAX_IDENTITIES", 1):
            result = self.memory.MemoryStore("telegram:second").add("fakta kedua")
        self.assertIn("batas", result.lower())

    def test_safe_profile_cannot_access_file_or_shell(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        names = {item["function"]["name"] for item in executor.schemas}
        self.assertIn("add_memory", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("run_shell", names)
        self.assertIn("tidak diizinkan", executor.run("run_shell", {"command": "id"}))

    def test_safe_profile_has_web_tools(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        names = {item["function"]["name"] for item in executor.schemas}
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_runtime_info_reports_non_secret_self_configuration(self):
        cfg = self.config.config_copy()
        cfg["provider"].update({"base_url": "https://provider.example/v1", "api_key": "never-print-this", "model": "model-x", "protocol": "openai"})
        self.config.save_config(cfg)
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)

        result = executor.run("runtime_info", {})

        self.assertIn("model-x", result)
        self.assertIn("provider.example", result)
        self.assertIn("openai", result)
        self.assertNotIn("never-print-this", result)
        self.assertIn("runtime_info", {item["function"]["name"] for item in executor.schemas})

    def test_seeded_self_analysis_skill_is_available(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        content = skills.load_skill("self-analysis")
        self.assertIn("runtime_info", content)
        self.assertIn("API key", content)

    def test_seeded_response_formatting_skill_is_available(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        content = skills.load_skill("response-formatting")
        self.assertIn("**bold**", content)
        self.assertIn("```bash", content)
        self.assertIn("```html", content)
        self.assertIn("jangan mengarang", content.lower())

    def test_system_prompt_contains_default_response_formatting_rules(self):
        self.assertIn("**bold**", self.config.SYSTEM_PROMPT)
        self.assertIn("fenced code block", self.config.SYSTEM_PROMPT)
        self.assertIn("hasil terminal", self.config.SYSTEM_PROMPT.lower())

    def test_web_fetch_blocks_internal_addresses(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "http://192.168.1.1/"):
            self.assertIn("diblokir", executor.run("web_fetch", {"url": url}))
        self.assertIn("diblokir", executor.run("web_fetch", {"url": "http://localhost.localdomain/"}))
        self.assertIn("ERROR", executor.run("web_fetch", {"url": "ftp://example.com/file"}))

    def test_workspace_profile_blocks_path_escape(self):
        workspace = self.home / "workspace"
        workspace.mkdir(parents=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=workspace)
        result = executor.run("write_file", {"path": "../outside.txt", "content": "no"})
        self.assertIn("workspace", result)
        self.assertFalse((self.home / "outside.txt").exists())

    def test_safe_profile_cannot_load_owner_private_skill(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        skills.save_skill("owner-secret-procedure", "# Private\n\n> Jangan bocorkan.\n\nPRIVATE-SKILL-CONTENT-CHECK")

        public_agent_tools = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        owner_tools = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.home)

        self.assertIn("tidak ditemukan", public_agent_tools.run("load_skill", {"name": "owner-secret-procedure"}))
        self.assertIn("PRIVATE-SKILL-CONTENT-CHECK", owner_tools.run("load_skill", {"name": "owner-secret-procedure"}))

    def test_webhook_requires_token_and_keeps_identity_namespaced(self):
        webhook = importlib.import_module("zeline.gateways.webhook")
        port = free_port()
        token = "this-is-a-long-webhook-test-token"
        received = []

        class FakeSessions:
            def send(self, **kwargs):
                received.append(kwargs)
                return f"reply:{kwargs['text']}"

        stop = threading.Event()
        thread = threading.Thread(
            target=webhook.start,
            args=(
                FakeSessions(),
                {"host": "127.0.0.1", "port": port, "token": token, "tool_profile": "safe"},
                stop,
            ),
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
                conn.request("GET", "/health")
                response = conn.getresponse()
                response.read()
                conn.close()
                if response.status == 200:
                    break
            except OSError:
                time.sleep(0.03)
        else:
            self.fail("webhook server tidak siap")

        body = json.dumps({"chat_id": "alice", "text": "halo"})
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("POST", "/message", body=body, headers={"Content-Type": "application/json"})
        unauthorized = conn.getresponse()
        self.assertEqual(unauthorized.status, 401)
        unauthorized.read()
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request(
            "POST",
            "/message",
            body=body,
            headers={"Content-Type": "application/json", "X-Zeline-Token": token},
        )
        authorized = conn.getresponse()
        payload = json.loads(authorized.read())
        conn.close()
        self.assertEqual(authorized.status, 200)
        self.assertEqual(payload["reply"], "reply:halo")
        self.assertEqual(received[0]["identity"], "webhook:alice")
        self.assertEqual(received[0]["tool_profile"], "safe")

        stop.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_telegram_config_and_message_split_helpers(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertTrue(telegram.validate_config({"token": "x", "tool_profile": "safe", "allowed": []}))
        self.assertEqual(telegram.validate_config({"token": "123:abc", "tool_profile": "safe", "allowed": []}), [])
        self.assertTrue(telegram._allowed(123, []))
        self.assertFalse(telegram._allowed(123, [456]))
        parts = telegram._split_message("a" * 8_010)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 4_000 for part in parts))

    def test_telegram_full_profile_requires_owner_allowlist(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        errors = telegram.validate_config({"token": "123:abc", "tool_profile": "full", "allowed": []})
        self.assertTrue(any("allowlist" in error.lower() for error in errors))
        self.assertEqual(telegram.validate_config({"token": "123:abc", "tool_profile": "full", "allowed": [7387183839]}), [])

    def test_telegram_working_status_matches_hermes_style(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._working_status_text(125), "⏳ Working — 2 min — waiting for provider response")

    def test_telegram_working_heartbeat_reports_until_turn_finishes(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        done = threading.Event()
        with mock.patch.object(telegram, "_api_call") as api:
            worker = telegram._start_working_heartbeat("bot-api", 42, done, interval=0.01)
            time.sleep(0.025)
            done.set()
            worker.join(timeout=1)
        self.assertGreaterEqual(api.call_count, 1)
        self.assertIn("Working", api.call_args.kwargs["text"])

    def test_telegram_renders_safe_markdown_as_html(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "## Judul\n**Penting** pakai `zeline doctor`.\n```html\n<div>aman</div>\n```"
        rendered = telegram._markdown_to_telegram_html(source)
        self.assertIn("<b>Judul</b>", rendered)
        self.assertIn("<b>Penting</b>", rendered)
        self.assertIn("<code>zeline doctor</code>", rendered)
        self.assertIn('<pre><code class="language-html">&lt;div&gt;aman&lt;/div&gt;</code></pre>', rendered)
        self.assertNotIn("<div>aman</div>", rendered)

    def test_telegram_agent_reply_uses_html_parse_mode(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **_kwargs): return "**Berhasil**"

        with mock.patch.object(telegram, "_api_call") as api:
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="hi", tool_profile="safe")
        self.assertEqual(api.call_args.kwargs["parse_mode"], "HTML")
        self.assertIn("<b>Berhasil</b>", api.call_args.kwargs["text"])

    def test_whatsapp_adapts_common_markdown(self):
        whatsapp = importlib.import_module("zeline.gateways.whatsapp")
        rendered = whatsapp._markdown_to_whatsapp("## Judul\n**Penting** dan `zeline doctor`\n```bash\nzeline status\n```")
        self.assertIn("*Judul*", rendered)
        self.assertIn("*Penting*", rendered)
        self.assertIn("`zeline doctor`", rendered)
        self.assertIn("```bash", rendered)

    def test_telegram_registers_hermes_style_command_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        commands = telegram._telegram_commands()
        self.assertEqual([item["command"] for item in commands], ["start", "model", "status", "stop", "new", "steer"])
        self.assertEqual(commands[0]["description"], "Start Zeline")
        self.assertIn("active turn", commands[3]["description"].lower())

    def test_telegram_status_reports_hermes_style_runtime_and_coding_tools(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def status(self, identity):
                self.identity = identity
                return {
                    "session_id": "zel-abc123",
                    "title": "Bangun aplikasi",
                    "created": "2026-08-09 10:00:00",
                    "last_activity": "2026-08-09 10:05:00",
                    "model": "deepseek-v4-flash",
                    "context": "12 messages",
                    "agent_running": True,
                }

        sessions = Sessions()
        with mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/status", sessions, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="full",
            )
        self.assertTrue(handled)
        text = api.call_args.kwargs["text"]
        self.assertEqual(sessions.identity, "telegram:42")
        self.assertEqual(text, (
            "📊 Zeline Gateway Status\n\n"
            "Session ID: zel-abc123\n"
            "Title: Bangun aplikasi\n"
            "Created: 2026-08-09 10:00:00\n"
            "Last Activity: 2026-08-09 10:05:00\n"
            "Model: deepseek-v4-flash\n"
            "Context: 12 messages\n"
            "Agent Running: Yes\n\n"
            "Connected Platforms: Telegram"
        ))

    def test_full_profile_exposes_coding_toolchain(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        names = [schema["function"]["name"] for schema in executor.schemas]
        for expected in ("read_file", "write_file", "edit_file", "patch_file", "search_files", "update_task", "run_shell"):
            self.assertIn(expected, names)

    def test_full_profile_patch_and_task_tools_execute_real_actions(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        self.assertIn("OK", executor.run("write_file", {"path": "app.py", "content": "name = 'old'\n"}))
        patched = executor.run("patch_file", {"path": "app.py", "old_text": "'old'", "new_text": "'new'"})
        self.assertIn("dipatch", patched)
        self.assertEqual((self.home / "app.py").read_text(), "name = 'new'\n")
        task = executor.run("update_task", {"task": "Run tests", "status": "in_progress"})
        self.assertIn("Run tests", task)
        self.assertIn("in_progress", task)

    def test_telegram_tool_progress_uses_hermes_style_labels_and_argument_preview(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._tool_progress_text("load_skill", {"name": "test-driven-development"}), "📚 Reading skill test-driven-development")
        shell = telegram._tool_progress_text("run_shell", {"command": "python -m unittest tests.test_agent"})
        self.assertTrue(shell.startswith("🖥️ Zeline Terminal\n<pre>"))
        self.assertIn("●  ●  ●", shell)
        self.assertIn("Bash", shell)
        self.assertIn("$ python -m unittest tests.test_agent", shell)
        self.assertTrue(shell.endswith("</pre>"))
        match = re.search(r"<pre>(.*?)</pre>", shell, re.DOTALL)
        self.assertIsNotNone(match)
        raw = match.group(1) if match else ""
        header = html.unescape(raw.splitlines()[1])
        self.assertEqual(header.index("Bash") + len("Bash") / 2, len(header) / 2)
        self.assertEqual(telegram._tool_progress_text("read_file", {"path": "zeline/agent.py"}), "📖 Reading <code>zeline/agent.py</code>")
        self.assertEqual(telegram._tool_progress_text("write_file", {"path": "app.py"}), "✍️ Writing <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("edit_file", {"path": "app.py"}), "✏️ Editing <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("patch_file", {"path": "app.py"}), "🩹 Patching <code>app.py</code>")
        task = telegram._tool_progress_text("update_task", {"task": "Run tests", "status": "in_progress"})
        self.assertEqual(task, "📋 Updating tasks\n<code>in_progress</code> · Run tests")

    def test_telegram_sends_each_tool_progress_as_separate_html_message(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **kwargs):
                kwargs["on_tool"]("run_shell", {"command": "python app.py"})
                kwargs["on_tool"]("edit_file", {"path": "app.py"})
                return "done"

        with mock.patch.object(telegram, "_api_call") as api:
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="code", tool_profile="full")
        progress = [call for call in api.call_args_list if len(call.args) > 1 and call.args[1] == "sendMessage"]
        self.assertEqual(len(progress), 3)  # shell, edit, final
        self.assertEqual(progress[0].kwargs["parse_mode"], "HTML")
        self.assertIn("🖥️ Zeline Terminal", progress[0].kwargs["text"])
        self.assertIn("✏️ Editing", progress[1].kwargs["text"])

    def test_telegram_model_picker_marks_current_model_and_uses_short_callbacks(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        models = ["vendor/model-a", "vendor/model-b"]
        text, markup = telegram._model_picker_payload(models, "vendor/model-b")
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        self.assertIn("Current: vendor/model-b", text)
        self.assertEqual([button["callback_data"] for button in buttons[:2]], ["model:0", "model:1"])
        self.assertEqual(buttons[1]["text"], "✓ model-b")
        self.assertLessEqual(max(len(button["callback_data"]) for button in buttons), 64)

    def test_telegram_model_root_picker_lists_named_providers_first(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        providers = [
            {"slug": "token-harbor", "name": "Token Harbor", "model": "model-a"},
            {"slug": "nvidia", "name": "NVIDIA NIM", "model": "model-b"},
        ]
        text, markup = telegram._provider_picker_payload(providers, "token-harbor")
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        self.assertIn("Select a provider", text)
        self.assertEqual(buttons[0], {"text": "✓ Token Harbor", "callback_data": "provider:0"})
        self.assertEqual(buttons[1], {"text": "NVIDIA NIM", "callback_data": "provider:1"})

    def test_telegram_provider_callback_opens_models_with_back_button(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        providers = [{"slug": "token-harbor", "name": "Token Harbor", "model": "model-a", "base_url": "https://api.example/v1", "api_key": "key"}]
        callback = {"id": "cb-1", "data": "provider:0", "message": {"message_id": 9, "chat": {"id": 42}}}
        with mock.patch.object(telegram, "_configured_providers", return_value=providers), mock.patch.object(telegram, "_discover_provider_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_callback("bot-api", callback, object())
        edit = api.call_args_list[-1]
        self.assertEqual(edit.args[1], "editMessageText")
        self.assertIn("Provider: Token Harbor", edit.kwargs["text"])
        buttons = [button for row in edit.kwargs["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn({"text": "« Back", "callback_data": "provider:back"}, buttons)

    def test_telegram_model_command_without_argument_opens_inline_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_discover_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/model", object(), "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(api.call_args.args[1], "sendMessage")
        self.assertIn("inline_keyboard", api.call_args.kwargs["reply_markup"])

    def test_telegram_model_callback_switches_model_and_edits_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        self.config.save_config(cfg)

        class Sessions:
            def __init__(self): self.reset_id = None
            def reset(self, identity): self.reset_id = identity; return True

        sessions = Sessions()
        callback = {"id": "cb-1", "data": "model:1", "message": {"message_id": 9, "chat": {"id": 42}}}
        with mock.patch.object(telegram, "_discover_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_callback("bot-api", callback, sessions)
        self.assertEqual(self.config.config_copy()["provider"]["model"], "model-b")
        self.assertEqual(sessions.reset_id, "telegram:42")
        methods = [call.args[1] for call in api.call_args_list]
        self.assertEqual(methods, ["answerCallbackQuery", "editMessageText"])

    def test_telegram_stop_cancels_active_turn_without_stopping_gateway(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def stop(self, identity): self.stopped = identity; return True

        sessions = Sessions()
        gateway_stop = threading.Event()
        with mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/stop", sessions, "telegram:42", 42,
                stop_event=gateway_stop, tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(sessions.stopped, "telegram:42")
        self.assertFalse(gateway_stop.is_set())
        self.assertIn("Stopped", api.call_args.kwargs["text"])

    def test_telegram_new_stops_old_turn_and_resets_session(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def stop(self, identity): self.stopped = identity; return True
            def reset(self, identity): self.reset_id = identity; return True

        sessions = Sessions()
        with mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/new", sessions, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(sessions.stopped, "telegram:42")
        self.assertEqual(sessions.reset_id, "telegram:42")
        self.assertIn("New session started", api.call_args.kwargs["text"])

    def test_telegram_steer_targets_active_turn_or_runs_normally_when_idle(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def __init__(self, accepted): self.accepted = accepted
            def steer(self, identity, text): self.steer_args = (identity, text); return self.accepted

        active = Sessions(True)
        with mock.patch.object(telegram, "_api_call") as api, mock.patch.object(telegram, "_start_agent_reply") as start_reply:
            handled = telegram._handle_command_update(
                "bot-api", "/steer fokus ke bug", active, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(active.steer_args, ("telegram:42", "fokus ke bug"))
        self.assertIn("Steer queued", api.call_args.kwargs["text"])
        start_reply.assert_not_called()

        idle = Sessions(False)
        with mock.patch.object(telegram, "_api_call"), mock.patch.object(telegram, "_start_agent_reply") as start_reply:
            telegram._handle_command_update(
                "bot-api", "/steer fokus ke bug", idle, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        start_reply.assert_called_once_with(
            "bot-api", idle, chat_id=42, identity="telegram:42",
            text="fokus ke bug", tool_profile="safe",
        )

    def test_telegram_model_command_persists_model_and_resets_session(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        self.config.save_config(cfg)

        class Sessions:
            def __init__(self): self.reset_id = None
            def reset(self, identity): self.reset_id = identity; return True

        sessions = Sessions()
        reply = telegram._handle_command("/model vendor/new-model", sessions, "telegram:42", stop_event=threading.Event())
        self.assertIn("vendor/new-model", reply)
        self.assertEqual(sessions.reset_id, "telegram:42")
        self.assertEqual(self.config.config_copy()["provider"]["model"], "vendor/new-model")

    def test_telegram_accepts_zip_larger_than_legacy_256_kb_limit(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        payload = b"z" * (729 * 1024)
        document = {"file_name": "SUPERAGENT-V7-FORSALE-FINAL.zip", "file_size": len(payload), "file_id": "zip-file"}
        response = mock.Mock(ok=True, content=payload)
        with mock.patch.object(telegram, "_api_call", return_value={"result": {"file_path": "documents/zip-file"}}), \
             mock.patch.object(telegram.requests, "get", return_value=response):
            content, error = telegram._download_document("https://api.telegram.org/bottoken", "token", document)
        self.assertIsNone(error)
        self.assertEqual(content, payload)

    def test_telegram_extracts_zip_with_more_than_legacy_64_text_files(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        import io, zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for index in range(65):
                archive.writestr(f"skills/skill-{index}.md", f"skill {index}")
        text, error = telegram._extract_document_text("skills.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        self.assertIn("skill 64", text)

    def test_telegram_truncates_extracted_zip_text_to_message_limit(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        import io, zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("skills/large.md", "x" * 20_000)
        text, error = telegram._extract_document_text("skills.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        prompt = telegram._build_document_prompt("skills.zip", text)
        self.assertLessEqual(len(prompt), 16_000)

    def test_telegram_extracts_text_and_safe_zip_entries(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        import io, zipfile
        text, error = telegram._extract_document_text("notes.md", b"# Hello\nZeline", "text/markdown")
        self.assertIsNone(error)
        self.assertIn("Hello", text)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("notes.txt", "inside archive")
            archive.writestr("../escape.txt", "must not appear")
        text, error = telegram._extract_document_text("notes.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        self.assertIn("inside archive", text)
        self.assertNotIn("must not appear", text)

    def test_telegram_identifies_images_without_claiming_visual_analysis(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        text, error = telegram._extract_document_text("image.png", b"not-a-real-image", "image/png")
        self.assertIsNone(error)
        self.assertIn("image", text.lower())
        self.assertIn("vision-capable", text.lower())

    def test_whatsapp_adapter_declares_validation_and_safe_defaults(self):
        whatsapp = importlib.import_module("zeline.gateways.whatsapp")
        self.assertEqual(whatsapp.validate_config({"enabled": True, "allowed": [], "tool_profile": "safe"}), [])
        self.assertTrue(whatsapp.validate_config({"enabled": True, "allowed": "bad", "tool_profile": "safe"}))
        self.assertTrue(whatsapp.validate_config({"enabled": True, "allowed": [], "tool_profile": "safe", "callback_port": "bad"}))
        bridge = whatsapp.render_bridge("bridge-test-token")
        self.assertIn("fromMe", bridge)
        self.assertIn("x-zeline-bridge", bridge.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
