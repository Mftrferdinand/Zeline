"""Operator-approved Telegram icons; presentation only, no real tool execution."""
import importlib
import unittest
import threading
from types import SimpleNamespace
from unittest import mock


class RestoredLifecycleContracts(unittest.TestCase):
    def setUp(self):
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_stop_exact_copy(self):
        sessions = mock.Mock()
        sessions.stop.return_value = True
        with mock.patch.object(self.telegram, "_api_call") as api:
            self.telegram._handle_command_update("fixture", "/stop", sessions,
                "telegram:fixture-stop", 1, stop_event=threading.Event(), tool_profile="safe")
        self.assertEqual(api.call_args.kwargs["text"],
                         "⛔ Stopped. You can continue this session.")

    def test_self_phases_are_evidence_only_and_separate(self):
        phases = ("📙 Self-Enhancement", "📘 Self-Improvement", "📗 Self-Refinement")
        for phase in phases:
            summary = phase.split(" ", 1)[0] + " <b>" + phase.split(" ", 1)[1] + ":</b> verified evidence."
            self.assertEqual(list(self.telegram._learning_report_parts((), summary)), [summary])
        self.assertEqual(list(self.telegram._learning_report_parts((), None)), [])
        for tool, args in (("manage_skill", {"action": "create"}),
                           ("manage_skill", {"action": "patch"}),
                           ("manage_skill", {"action": "write_file"}),
                           ("add_memory", {}), ("resolve_lesson", {})):
            self.assertIsNone(self.telegram._tool_result_text(tool, args, "OK, verified"))

    def test_question_card_has_letter_choices_and_owned_receipt(self):
        entry = SimpleNamespace(question="Choose fixture", options=("Full option A", "Full option B"),
            request_id="fixture-id", message_id=None, status="pending", answer="", event=threading.Event())
        with mock.patch.object(self.telegram, "_api_call", return_value={"result": {"message_id": 77}}) as api:
            self.telegram._render_ask_question("fixture", 1, entry)
            payload = api.call_args.kwargs
            self.assertIn("❓Ask user: Choose fixture", payload["text"])
            self.assertIn("A. Full option A", payload["text"])
            self.assertEqual([row[0]["text"] for row in payload["reply_markup"]["inline_keyboard"]],
                             ["A", "B", "Fill in yourself"])
            entry.answer, entry.status = "Full option B", "answered"
            entry.event.set()
            entry.on_resolved(entry)
            self.assertEqual(api.call_args.args[1], "editMessageText")
            self.assertEqual(api.call_args.kwargs["message_id"], 77)
            self.assertIn("Answer: Full option B", api.call_args.kwargs["text"])
            self.assertEqual(api.call_args.kwargs["reply_markup"], {"inline_keyboard": []})

    def test_reflection_reports_after_final_without_reply_quote(self):
        sessions = mock.Mock()
        sessions.send.return_value = "Final fixture answer"
        sessions.reflect.return_value = "📘 <b>Self-Improvement:</b> verified revision."
        with mock.patch.object(self.telegram, "_api_call", return_value={"ok": True}) as api, \
             mock.patch.object(self.telegram, "_LiveStatus"), \
             mock.patch.object(self.telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
             mock.patch.object(self.telegram.threading, "Thread", side_effect=lambda **kw: mock.Mock(start=kw["target"])):
            self.telegram._send_agent_reply("fixture", sessions, chat_id=1, identity="telegram:fixture",
                text="Fixture", tool_profile="safe", reply_to_message_id=42)
        messages = [c.kwargs for c in api.call_args_list if c.args[1] == "sendMessage"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["text"], "Final fixture answer")
        self.assertEqual(messages[0]["reply_to_message_id"], 42)
        self.assertIn("📘", messages[1]["text"])
        self.assertNotIn("reply_to_message_id", messages[1])


class TelegramIconContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_all_requested_activity_labels(self):
        cases = (
            ("browser", {"action": "open"}, "🌐 Opening webpage"),
            ("browser", {}, "🌐 Driving the browser"),
            ("browser", {"action": "type"}, "⚡ Entering text"),
            ("browser", {"action": "click"}, "🗃️ Interacting with webpage"),
            ("browser", {"action": "eval"}, "📂 Executing browser script"),
            ("browser", {"action": "screenshot"}, "📸 Capturing webpage screenshot"),
            ("browser", {"action": "close"}, "📴 Closing browser session"),
            ("browser", {"action": "links"}, "🖇️ Extracting page links"),
            ("http_request", {}, "🖇️ Sending API request"),
            ("code_intel", {"action": "references"}, "🖇️ Finding symbol references"),
            ("search_files", {}, "🔎 Searching workspace"),
            ("web_search", {}, "🔎 Searching the web"),
            ("git", {"action": "show"}, "🔎 Inspecting commit"),
            ("deep_research", {}, "🔍 Researching in depth"),
            ("add_memory", {}, "🔍 Saving to memory"),
            ("list_memory", {}, "🔍 Retrieving saved memories"),
            ("read_file", {}, "📖 Reading file"),
            ("browser", {"action": "text"}, "📖 Reading webpage content"),
            ("download_file", {}, "📥 Downloading a file"),
            ("run_shell", {"command": "printf demo"}, "💻 Executing command"),
            ("execute_code", {"code": "print('demo')"}, "🐍 Executing code"),
            ("write_file", {}, "📝 Writing file"),
            ("manage_skill", {"action": "patch", "name": "demo"}, "📝 Updating skill"),
            ("edit_file", {}, "📝 Editing file"),
            ("patch_file", {}, "📝 Editing file"),
            ("manage_skill", {"action": "write_file"}, "📝 Writing skill resource"),
            ("undo_file", {"action": "list"}, "⏱️ Reviewing file checkpoints"),
            ("undo_file", {"action": "diff"}, "🔍 Previewing file changes"),
            ("git", {"action": "diff"}, "🔍 Reviewing code changes"),
            ("undo_file", {"action": "restore"}, "✂️ Restoring previous version"),
            ("undo_file", {}, "✂️ Undoing a file change"),
            ("code_intel", {"action": "diagnostics"}, "⚙️ Analyzing code diagnostics"),
            ("code_intel", {"action": "servers"}, "⚙️ Checking language servers"),
            ("code_intel", {"action": "definition"}, "📍 Locating symbol definition"),
            ("code_intel", {"action": "hover"}, "💡 Inspecting type information"),
            ("code_intel", {"action": "symbols", "path": "demo.py"}, "🗺 Mapping symbols"),
            ("code_intel", {}, "🧭 Asking the language server"),
            ("system_env", {}, "🖥️ Inspecting system environment"),
            ("run_shell", {"command": "printf demo", "background": True}, "🌟 Starting background process"),
            ("process_control", {"action": "list"}, "📋 Listing active processes"),
            ("update_task", {}, "📋 Updating task progress"),
            ("process_control", {"action": "poll"}, "🛄 Checking process status"),
            ("process_control", {"action": "log"}, "📖 Reading process logs"),
            ("git", {"action": "log"}, "📖 Reading commit history"),
            ("process_control", {"action": "kill"}, "🛑 Terminating process"),
            ("process_control", {}, "⚙️ Managing background process"),
            ("custom_fixture_tool", {}, "⚙️ Executing tool"),
            ("network_route", {"action": "list"}, "🛠️ Listing network routes"),
            ("network_route", {"action": "remove"}, "🛠️ Removing network route"),
            ("network_route", {}, "🛠️ Managing network routes"),
            ("network_route", {"action": "add"}, "🛜 Configuring network route"),
            ("network_route", {"action": "test"}, "🛜 Testing network connection"),
            ("git", {"action": "status"}, "📁 Checking repository status"),
            ("git", {}, "📁 Running git"),
            ("git", {"action": "branch"}, "🗳️ Listing repository branches"),
            ("git", {"action": "add"}, "📦 Staging changes"),
            ("git", {"action": "commit"}, "🗳️ Committing changes"),
            ("schedule_task", {"action": "add"}, "⏰ Scheduling automation"),
            ("schedule_task", {}, "⏰ Managing scheduled jobs"),
            ("schedule_task", {"action": "list"}, "📅 Reviewing scheduled tasks"),
            ("schedule_task", {"action": "show"}, "📅 Reviewing scheduled task"),
            ("schedule_task", {"action": "pause"}, "🅿️ Pausing scheduled task"),
            ("schedule_task", {"action": "resume"}, "⏸️ Resuming scheduled task"),
            ("schedule_task", {"action": "run"}, "⏸️ Running scheduled task"),
            ("schedule_task", {"action": "remove"}, "🗑 Removing scheduled job"),
            ("manage_skill", {"action": "delete"}, "🗑 Removing skill"),
            ("load_skill", {"name": "demo"}, "📚 Loading skill"),
            ("manage_skill", {"action": "list"}, "🗃️ Browsing available skills"),
            ("manage_skill", {"action": "create"}, "✨ Creating skill"),
            ("manage_skill", {"action": "delete", "absorbed_into": "other"}, "🧬 Merging skills"),
            ("resolve_lesson", {}, "🎓 Resolving a lesson"),
            ("remove_memory", {}, "🌀 Removing memory"),
            ("recall_history", {}, "⏱️ Recalling conversation history"),
            ("delegate_task", {}, "⚓ Delegating task"),
            ("ask_user", {}, "🙋 Requesting your input"),
            ("runtime_info", {}, "🪪 Checking agent runtime"),
            ("mcp__fixture__lookup", {}, "🛜 Connecting to external tool"),
            ("generate_image", {}, "🎨 Generating image"),
            ("analyze_media", {"path_or_url": "fixture.png"}, "👀 Analyzing image"),
            ("analyze_media", {"path_or_url": "fixture.mp4"}, "🎞️ Analyzing video"),
            ("analyze_media", {"path_or_url": "fixture.ogg"}, "🎧 Analyzing audio"),
            ("send_file", {}, "📤 Sending a file"),
        )
        for tool, arguments, expected in cases:
            with self.subTest(tool=tool, arguments=arguments):
                self.assertTrue(self.telegram._tool_progress_text(tool, arguments).startswith(expected), expected)

    def test_changed_research_icon_keeps_collapse_and_finalization(self):
        line = self.telegram._tool_progress_text("deep_research", {"query": "demo"})
        self.assertEqual(self.telegram._progress_category(line), "research")
        self.assertEqual(self.telegram._finalize_line(line), "📖 Read web · demo")
        self.assertIsNone(self.telegram._progress_category("🔍 Saving to memory…"))
        self.assertIsNone(self.telegram._progress_category("🔍 Previewing file changes…"))

    def test_interrupt_icon_changes_without_changing_message(self):
        self.assertEqual(self.telegram._interrupt_ack({"elapsed": 2}),
                         "💥 Interrupting current task (2s elapsed). I'll respond to your message shortly.")

    def test_existing_work_wait_and_learning_labels_are_preserved(self):
        self.assertTrue(self.telegram._working_status_text(0).startswith("🧑🏻‍💻 Working"))
        self.assertTrue(self.telegram._working_status_text(0, working=False).startswith("💭 Thinking"))
        self.assertTrue(self.telegram._provider_wait_text(0).startswith("⏳ Waiting"))


"""Isolated narration tests: no agent imports, operator state, or tool execution."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "zeline" / "narration.py"


def new_gate():
    assert MODULE_PATH.is_file(), "NarrationGate module has not been implemented"
    spec = importlib.util.spec_from_file_location("isolated_narration", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NarrationGate()


class NarrationGateTests(unittest.TestCase):
    def test_preserves_finding_and_whitespace_exactly(self):
        gate = new_gate()
        text = "  Password salah.\n  Perlu kredensial baru.  "
        self.assertEqual(gate.select(text), text)

    def test_suppresses_only_recognized_routine_announcements(self):
        for text in (
            "saya buka dulu webnya", "oke gua coba masukan pw",
            "Gua cek dulu.", "Saya klik tombolnya.", "Aku ketik passwordnya.",
            "I'll open the website.", "I will click the button.",
            "I will type the password.", "I will read the file.",
            "Oke.", "OK!", "  \n  ",
        ):
            with self.subTest(text=text):
                self.assertIsNone(new_gate().select(text))

    def test_suppresses_multiline_only_when_every_line_is_routine(self):
        self.assertIsNone(new_gate().select("Oke.\n\nSaya klik tombolnya.\nGua cek dulu."))

    def test_preserves_important_mixed_unknown_and_decorated_content(self):
        for text in (
            "Saya buka dulu webnya, karena sesi sebelumnya kedaluwarsa.",
            "Saya klik tombolnya. Password salah.",
            "Saya klik tombolnya.\nPassword salah.",
            "Saya klik tombolnya?", "Boleh saya klik tombolnya?",
            '"Saya klik tombolnya."', "`Saya klik tombolnya.`",
            "```\nSaya klik tombolnya.\n```", "Error: Saya klik tombolnya.",
            "Saya buka file untuk memeriksa risiko sebelum migrasi.",
            "I will open the website to verify the authentication flow.",
            "I will delete the database.", "I will click the button after approval.",
            "Saya cek OTP dan verifikasi akun.", "Password salah",
            "Saya akan beralih ke API karena browser diblokir.",
            "Je vais ouvrir le site.", "网页打不开", "🔍 Saya klik tombolnya.",
            "🔍 Password salah.", "OK — verification requires your input.",
            "Saya klik tombolnya; saldo 100 menjadi 200.",
            "Saya klik tombolnya... gagal.", "Saya klik tombolnya! Lalu berhenti.",
            "I will not open the website.", "Saya klik tombolnya\n401 Unauthorized",
        ):
            with self.subTest(text=text):
                self.assertEqual(new_gate().select(text), text)

    def test_exact_dedup_is_turn_local_and_does_not_normalize_facts(self):
        gate = new_gate()
        text = "Password salah."
        self.assertEqual(gate.select(text), text)
        self.assertIsNone(gate.select(text))
        self.assertEqual(new_gate().select(text), text)
        for changed in ("Password salah!", " password salah. ",
                        "Saldo 100.", "Saldo 101.", "🔍 Saldo 102.", "🔍 Saldo 103."):
            self.assertEqual(gate.select(changed), changed)
        self.assertEqual(gate.select(text), text)

    def test_reemits_finding_after_another_meaningful_update(self):
        for first, intervening in (
            ("Finding A", "Finding B"),
            ("Login failed: session expired.", "Login succeeded; session restored."),
            ("Finding A", "Finding " + "x" * 4096),
        ):
            with self.subTest(first=first, intervening_length=len(intervening)):
                gate = new_gate()
                self.assertEqual(gate.select(first), first)
                self.assertEqual(gate.select(intervening), intervening)
                self.assertEqual(gate.select(first), first)
                self.assertIsNone(gate.select(first))

    def test_suppressed_routine_does_not_reset_consecutive_dedup(self):
        gate = new_gate()
        text = "Finding A"
        self.assertEqual(gate.select(text), text)
        self.assertIsNone(gate.select("Saya klik tombolnya."))
        self.assertIsNone(gate.select(text))

    def test_oversized_messages_are_not_retained(self):
        gate = new_gate()
        text = "Finding " + "x" * 4096
        self.assertEqual(gate.select(text), text)
        self.assertEqual(gate.select(text), text)
        boundary = "x" * 4096
        self.assertEqual(gate.select(boundary), boundary)
        self.assertIsNone(gate.select(boundary))


if __name__ == "__main__":
    unittest.main()
