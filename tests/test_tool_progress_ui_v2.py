"""Contract tests for the Telegram Tool Progress UI v2 resolver.

Every registered tool must render a safe, meaningful activity line (except the
explicitly-hidden `web_fetch`), while action tools get their documented v2 label.
"""
from __future__ import annotations

import importlib
import unittest


class ToolProgressUiV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.telegram = importlib.import_module("zeline.gateways.telegram")
        cls.tools = importlib.import_module("zeline.tools")

    def test_every_registered_tool_has_a_visible_progress_label_or_explicit_hide(self):
        """A new tool cannot silently fall through to an unusable progress card."""
        examples = {
            "send_file": {"path": "report.pdf"},
            "git": {"action": "status"},
            "schedule_task": {"action": "list"},
            "runtime_info": {},
            "add_memory": {"fact": "test"},
            "remove_memory": {"substring": "test"},
            "list_memory": {},
            "load_skill": {"name": "testing"},
            "web_search": {"query": "agent framework"},
            "web_fetch": {"url": "https://example.com"},
            "network_route": {"action": "list"},
            "deep_research": {"query": "agent framework"},
            "analyze_media": {"path_or_url": "clip.mp4"},
            "generate_image": {"prompt": "a test image"},
            "http_request": {"url": "https://example.com/api"},
            "browser": {"action": "open", "url": "https://example.com"},
            "code_intel": {"action": "diagnostics", "path": "app.py"},
            "system_env": {},
            "read_file": {"path": "app.py"},
            "write_file": {"path": "app.py"},
            "edit_file": {"path": "app.py"},
            "patch_file": {"path": "app.py"},
            "search_files": {"query": "handler"},
            "download_file": {"url": "https://example.com/app.zip", "path": "app.zip"},
            "undo_file": {"action": "list"},
            "update_task": {"task": "Test UI", "status": "in_progress"},
            "manage_skill": {"action": "list"},
            "resolve_lesson": {"tool": "read_file"},
            "execute_code": {"code": "print('ok')"},
            "run_shell": {"command": "pytest -q"},
            "process_control": {"action": "list"},
            "delegate_task": {"goal": "review UI"},
            "recall_history": {},
            "ask_user": {"question": "Continue?"},
        }
        registry = {definition.name for definition in self.tools.TOOL_DEFS}
        self.assertEqual(registry, set(examples), "update UI examples with any new registry tool")
        for name in registry:
            with self.subTest(tool=name):
                rendered = self.telegram._tool_progress_text(name, examples[name])
                if name == "web_fetch":
                    self.assertEqual(rendered, "")
                else:
                    self.assertTrue(rendered.strip(), f"{name} rendered an empty progress line")
                    self.assertFalse(rendered.startswith("🔧"), f"{name} fell back instead of using its UI mapping")

    def test_v2_key_labels_and_emojis(self):
        cases = (
            ("load_skill", {"name": "testing"}, "📚 Loading skill: testing"),
            ("manage_skill", {"action": "list"}, "🗃️ Browsing available skills…"),
            ("manage_skill", {"action": "create", "name": "testing"}, "✨ Creating skill: <code>testing</code>"),
            ("remove_memory", {"substring": "stale"}, "🌀 Removing memory…"),
            ("search_files", {"query": "handler"}, "🔎 Searching workspace: handler"),
            ("undo_file", {"action": "list"}, "⏱️ Reviewing file checkpoints…"),
            ("run_shell", {"command": "pytest -q"}, "💻 Executing command… <pre>pytest -q</pre>"),
            ("process_control", {"action": "poll", "job_id": "job-1"}, "🛄 Checking process status <code>job-1</code>…"),
            ("system_env", {}, "🖥️ Inspecting system environment…"),
            ("git", {"action": "branch"}, "🗳️ Listing repository branches…"),
            ("web_search", {"query": "agent framework"}, "🔎 Searching the web: agent…"),
            ("deep_research", {"query": "agent framework"}, "🔍 Researching in depth: agent framework…"),
            ("network_route", {"action": "add", "label": "route-a"}, "🛜 Configuring network route: <code>route-a</code>"),
            ("browser", {"action": "close"}, "📴 Closing browser session…"),
            ("code_intel", {"action": "references", "path": "app.py", "line": 9}, "🖇️ Finding symbol references <code>app.py</code> L9"),
            ("analyze_media", {"path_or_url": "clip.mp4"}, "🎞️ Analyzing video…"),
            ("analyze_media", {"path_or_url": "voice.ogg"}, "🎧 Analyzing audio…"),
            ("analyze_media", {"path_or_url": "image.png"}, "👀 Analyzing image…"),
            ("schedule_task", {"action": "list"}, "📅 Reviewing scheduled tasks…"),
            ("runtime_info", {}, "🪪 Checking agent runtime…"),
            ("ask_user", {"question": "Continue?"}, "🙋 Requesting your input: Continue?"),
        )
        for name, arguments, expected in cases:
            with self.subTest(name=name, arguments=arguments):
                self.assertEqual(self.telegram._tool_progress_text(name, arguments), expected)


if __name__ == "__main__":
    unittest.main()
