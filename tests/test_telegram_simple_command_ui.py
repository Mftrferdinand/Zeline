import importlib
import threading
from unittest import mock


class TestTelegramSimpleCommandUI:
    @classmethod
    def setup_class(cls):
        cls.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_command_menu_matches_the_compact_setup_template(self):
        commands = self.telegram._telegram_commands()
        assert [item["command"] for item in commands] == [
            "model", "status", "version", "update", "undo", "stats",
            "events", "lessons", "self", "skillforge", "stop", "new",
        ]
        assert [item["description"] for item in commands] == [
            "Switch model", "View runtime status", "Show version", "Update version",
            "Checkpoints", "View token usage", "Recent file/skill/memory",
            "View self-learning", "Three-self evidence status", "Review skill", "Stop the active turn",
            "Start a new session",
        ]

    def test_start_reply_is_only_the_compact_command_list(self):
        expected = (
            "Command to setup :\n"
            "◉ /model — Switch model\n"
            "◉ /status — View runtime status\n"
            "◉ /version — Show version\n"
            "◉ /update — Update version\n"
            "◉ /undo — Checkpoints\n"
            "◉ /stats — View token usage\n"
            "◉ /events — Recent file/skill/memory\n"
            "◉ /lessons — View self-learning\n"
            "◉ /self — Three-self evidence status\n"
            "◉ /skillforge — Review skill\n"
            "◉ /stop — Stop the active turn\n"
            "◉ /new — Start a new session\n\n"
            "Send a message to start a task"
        )
        with mock.patch.object(self.telegram, "_api_call", return_value={"ok": True}) as api:
            handled = self.telegram._handle_command_update(
                "bot-api", "/start", object(), "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="full",
            )
        assert handled is True
        assert api.call_args.kwargs["text"] == expected

    def test_version_card_matches_the_compact_template(self):
        report = {
            "current": "0.4.4",
            "latest": "v0.5.0",
            "error": None,
            "up_to_date": False,
            "checkout": None,
            "updating": False,
        }
        with mock.patch("zeline.self_update.version_report", return_value=report):
            assert self.telegram._version_reply() == (
                "🗳 Zeline Version\n"
                "◉ Installed : 0.4.4\n"
                "◉ Latest : v0.5.0\n"
                "◉ Status : /update available\n"
                "◉ Platform : Telegram"
            )

    def test_gateway_status_card_matches_the_compact_template(self):
        class Sessions:
            def status(self, identity):
                assert identity == "telegram:42"
                return {
                    "session_id": "zel-abc123",
                    "title": "ignored title",
                    "model": "zai-org/GLM-5.3-Flash",
                    "context": "10 messages",
                    "agent_running": False,
                }

        with mock.patch.object(self.telegram, "_provider_label", return_value="river"), \
                mock.patch.object(self.telegram, "_api_call", return_value={"ok": True}) as api:
            handled = self.telegram._handle_command_update(
                "bot-api", "/status", Sessions(), "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="full",
            )
        assert handled is True
        assert api.call_args.kwargs["text"] == (
            "🌐 Zeline Gateway Status\n"
            "◉ Session ID : zel-abc123\n"
            "◉ Provider : river\n"
            "◉ Model : zai-org/GLM-5.3-Flash\n"
            "◉ Context : 10 messages\n"
            "◉ Agent Running : No\n"
            "◉ Platform : Telegram"
        )
        assert "Title" not in api.call_args.kwargs["text"]

    def test_simple_cards_do_not_use_ascii_borders_or_long_footer_prompts(self):
        with mock.patch.object(self.telegram, "_fetch_model_capabilities", return_value={}):
            cards = [
                self.telegram._model_switch_text("river/model", {"name": "river", "slug": "river"}),
                self.telegram._new_session_text(),
            ]
        for card in cards:
            assert "╭" not in card
            assert "├" not in card
            assert "╰" not in card
            assert "◉" in card

    def test_help_uses_the_same_compact_command_list(self):
        assert self.telegram._handle_command(
            "/help", object(), "telegram:42", stop_event=threading.Event()
        ) == self.telegram._command_setup_text()