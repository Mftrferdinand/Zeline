"""Operator-approved Telegram icons; presentation only, no real tool execution."""
import importlib
import unittest


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
            ("http_request", {}, "⚡ Sending API request"),
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

    def test_http_request_progress_links_url_inside_label(self):
        urls = (
            "https://api.github.com/repos/gustiana08/codomo",
            "https://api.github.com/repos/gustiana08/codomo/contents/",
            "https://raw.githubusercontent.com/gustiana08/codomo/main/README.md",
            "https://raw.githubusercontent.com/gustiana08/codomo/main/package.json",
        )
        for url in urls:
            with self.subTest(url=url):
                line = self.telegram._tool_progress_text("http_request", {"url": url})
                expected = "⚡ <a href=" + chr(34) + url + chr(34) + ">Sending API request-Github</a>"
                self.assertEqual(line, expected)
                self.assertNotIn("🖇️", line)
                self.assertEqual(line.count(url), 1)

    def test_http_request_progress_uses_provider_labels_inside_link(self):
        cases = (
            ("https://api.openai.com/v1/models", "OpenAi"),
            ("https://api.anthropic.com/v1/messages", "Anthropic"),
            ("https://api.example.com/v1/models", "Example"),
        )
        for url, provider in cases:
            with self.subTest(url=url):
                line = self.telegram._tool_progress_text("http_request", {"url": url})
                expected = (
                    "⚡ <a href=" + chr(34) + url + chr(34)
                    + ">Sending API request-" + provider + "</a>"
                )
                self.assertEqual(line, expected)
                self.assertEqual(line.count(url), 1)

    def test_http_request_progress_escapes_href_and_rejects_credentials(self):
        url = "https://api.example.com/v1?a=1&b=2"
        line = self.telegram._tool_progress_text("http_request", {"url": url})
        self.assertIn("href=" + chr(34) + "https://api.example.com/v1?a=1&amp;b=2" + chr(34), line)
        self.assertIn(">Sending API request-Example</a>", line)
        self.assertNotIn("&b=2</a>", line)
        unsafe = self.telegram._tool_progress_text(
            "http_request", {"url": "https://user:pass@example.com/private"}
        )
        self.assertEqual(unsafe, "⚡ Sending API request…")

    def test_changed_research_icon_keeps_collapse_and_finalization(self):
        line = self.telegram._tool_progress_text("deep_research", {"query": "demo"})
        self.assertEqual(self.telegram._progress_category(line), "research")
        self.assertEqual(self.telegram._finalize_line(line), "📖 Read web · demo")
        self.assertIsNone(self.telegram._progress_category("🔍 Saving to memory…"))
        self.assertIsNone(self.telegram._progress_category("🔍 Previewing file changes…"))

    def test_interrupt_icon_changes_without_changing_message(self):
        self.assertEqual(self.telegram._interrupt_ack({"elapsed": 2}),
                         "💥 Interrupting current task (2s elapsed). I'll respond to your message shortly.")

    def test_work_wait_icons_and_review_only_learning_labels_are_preserved(self):
        self.assertTrue(self.telegram._working_status_text(0).startswith("🧑🏻‍💻 Working"))
        self.assertTrue(self.telegram._working_status_text(0, working=False).startswith("💭 Thinking"))
        self.assertTrue(self.telegram._provider_wait_text(0).startswith("⏳ Waiting"))
        self.assertEqual(self.telegram._LEARNING_PHASES,
                         ("📙 Self-Enhancement", "📘 Self-Improvement", "📗 Self-Refinement"))
        for action in ("create", "patch", "write_file", "delete", "list", "inventory"):
            with self.subTest(action=action):
                self.assertIsNone(self.telegram._tool_result_text("manage_skill", {"action": action}, "fixture"))
