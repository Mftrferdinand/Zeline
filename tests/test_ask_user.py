"""Contract tests for ask_user (human-in-the-loop mid-turn questions).

`ask_user` is the one tool that deliberately BLOCKS the agent. That makes it the
easiest place to wedge a session, so these tests pin the escape hatches as hard
as the happy path:

- an answer routed from the gateway unblocks the waiting tool
- a tapped option resolves by index
- /stop and /new release a pending question
- a timeout returns actionable guidance instead of hanging forever
- the wait can never outlive the turn budget
- only one question per identity may be open
- the tool is exposed in every profile but cannot be answered by another chat
"""
from __future__ import annotations

import contextlib
import importlib
import os
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


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    interaction = importlib.import_module("zeline.interaction")
    return cfg, interaction


class AskUserBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.interaction = fresh(self.home)

    def tearDown(self):
        # Never leak a pending question into the next test.
        with contextlib.suppress(Exception):
            self.interaction._PENDING.clear()
            self.interaction._CHANNELS.clear()
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class OptionNormalizationTests(AskUserBase):
    def test_options_are_trimmed_deduped_in_order_and_capped(self):
        raw = [" first ", "second", "", "   ", "First", "third", "fourth", "fifth", "sixth", "seventh"]
        result = self.interaction.normalize_options(raw)
        self.assertEqual(result[:3], ("first", "second", "third"))
        # "First" duplicates "first" case-insensitively and must be dropped.
        self.assertEqual(len([x for x in result if x.casefold() == "first"]), 1)
        self.assertLessEqual(len(result), self.interaction.MAX_OPTIONS)

    def test_a_bare_string_is_not_treated_as_a_list_of_choices(self):
        # "yes" must not become ('y','e','s') — a classic iterable-string bug.
        self.assertEqual(self.interaction.normalize_options("yes"), ())

    def test_none_and_non_iterable_are_free_form(self):
        self.assertEqual(self.interaction.normalize_options(None), ())
        self.assertEqual(self.interaction.normalize_options(42), ())

    def test_long_option_text_is_truncated(self):
        long_option = "x" * 500
        result = self.interaction.normalize_options([long_option])
        self.assertEqual(len(result[0]), self.interaction.MAX_OPTION_CHARS)


class AskAnswerTests(AskUserBase):
    def _ask_in_thread(self, identity="telegram:1", question="Which one?", options=None):
        box: dict[str, str] = {}

        def run():
            box["result"] = self.interaction.ask(identity, question, options)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        # Wait until the question is actually registered before acting on it.
        for _ in range(100):
            if self.interaction.has_pending(identity):
                break
            time.sleep(0.01)
        return worker, box

    def test_a_free_text_answer_unblocks_the_waiting_tool(self):
        worker, box = self._ask_in_thread()
        self.assertTrue(self.interaction.answer("telegram:1", "  use postgres  "))
        worker.join(timeout=5)
        self.assertEqual(box["result"], "use postgres")
        # The pending entry must be cleaned up, or the next message is swallowed.
        self.assertFalse(self.interaction.has_pending("telegram:1"))

    def test_tapping_an_option_answers_by_index(self):
        worker, box = self._ask_in_thread(options=["sqlite", "postgres", "mysql"])
        chosen = self.interaction.answer_option("telegram:1", 1)
        worker.join(timeout=5)
        self.assertEqual(chosen, "postgres")
        self.assertEqual(box["result"], "postgres")

    def test_an_out_of_range_option_index_is_rejected(self):
        worker, _box = self._ask_in_thread(options=["a", "b"])
        self.assertIsNone(self.interaction.answer_option("telegram:1", 9))
        self.assertIsNone(self.interaction.answer_option("telegram:1", -1))
        # Still waiting — a bad index must not resolve the question.
        self.assertTrue(self.interaction.has_pending("telegram:1"))
        self.interaction.cancel("telegram:1")
        worker.join(timeout=5)

    def test_another_chat_cannot_answer_someone_elses_question(self):
        worker, box = self._ask_in_thread(identity="telegram:1")
        self.assertFalse(self.interaction.answer("telegram:999", "malicious"))
        self.assertTrue(self.interaction.has_pending("telegram:1"))
        self.interaction.cancel("telegram:1")
        worker.join(timeout=5)
        self.assertIn("cancelled", box["result"].casefold())

    def test_cancel_releases_the_wait_so_stop_and_new_work(self):
        worker, box = self._ask_in_thread()
        self.assertTrue(self.interaction.cancel("telegram:1"))
        worker.join(timeout=5)
        self.assertIn("cancelled", box["result"].casefold())
        self.assertFalse(self.interaction.has_pending("telegram:1"))

    def test_cancel_on_nothing_pending_is_false_not_an_error(self):
        self.assertFalse(self.interaction.cancel("telegram:nobody"))

    def test_a_second_question_is_refused_rather_than_replacing_the_first(self):
        worker, _box = self._ask_in_thread(question="First?")
        second = self.interaction.ask("telegram:1", "Second?")
        self.assertIn("already awaiting", second.casefold())
        # The original question must survive the refusal.
        entry = self.interaction.pending("telegram:1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.question, "First?")  # type: ignore[union-attr]
        self.interaction.cancel("telegram:1")
        worker.join(timeout=5)

    def test_answering_twice_only_counts_once(self):
        worker, box = self._ask_in_thread()
        self.assertTrue(self.interaction.answer("telegram:1", "first"))
        worker.join(timeout=5)
        # Entry is gone; a late duplicate must not be accepted.
        self.assertFalse(self.interaction.answer("telegram:1", "second"))
        self.assertEqual(box["result"], "first")

    def test_empty_question_is_rejected_without_registering_a_wait(self):
        result = self.interaction.ask("telegram:1", "   ")
        self.assertIn("ERROR", result)
        self.assertFalse(self.interaction.has_pending("telegram:1"))

    def test_answer_is_truncated_so_it_cannot_flood_the_transcript(self):
        worker, box = self._ask_in_thread()
        self.interaction.answer("telegram:1", "y" * 10_000)
        worker.join(timeout=5)
        self.assertEqual(len(box["result"]), self.interaction.MAX_ANSWER_CHARS)


class TimeoutTests(AskUserBase):
    def test_timeout_returns_actionable_guidance_not_a_hang(self):
        saved = self.config.config_copy()
        saved["agent"]["ask_user_timeout"] = 1
        self.config.save_config(saved)
        started = time.monotonic()
        result = self.interaction.ask("telegram:1", "Anyone there?")
        elapsed = time.monotonic() - started
        self.assertIn("no answer", result.casefold())
        # It must tell the model what to do next, not just that it failed.
        self.assertIn("best judgement", result.casefold())
        self.assertLess(elapsed, 15)
        self.assertFalse(self.interaction.has_pending("telegram:1"))

    def test_wait_can_never_outlive_the_turn_budget(self):
        saved = self.config.config_copy()
        saved["agent"]["ask_user_timeout"] = 99_999
        self.config.save_config(saved)
        ceiling = float(self.config.MAX_TURN_SECONDS)
        self.assertLess(self.interaction._timeout_seconds(), ceiling)

    def test_garbage_timeout_falls_back_to_a_sane_value(self):
        with mock.patch.object(self.config, "ASK_USER_TIMEOUT", "not-a-number"):
            self.assertGreater(self.interaction._timeout_seconds(), 0)


class SynchronousChannelTests(AskUserBase):
    def test_a_registered_channel_can_answer_immediately(self):
        """The CLI answers at the keyboard — no event wait, no timeout."""
        seen: list[str] = []

        def renderer(entry):
            seen.append(entry.question)
            return "answered inline"

        self.interaction.register_channel("cli:local", renderer)
        started = time.monotonic()
        result = self.interaction.ask("cli:local", "Pick one", ["a", "b"])
        self.assertEqual(result, "answered inline")
        self.assertEqual(seen, ["Pick one"])
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(self.interaction.has_pending("cli:local"))

    def test_a_broken_channel_falls_back_to_waiting_instead_of_crashing(self):
        def renderer(_entry):
            raise RuntimeError("renderer exploded")

        self.interaction.register_channel("telegram:1", renderer)
        box: dict[str, str] = {}

        def run():
            box["result"] = self.interaction.ask("telegram:1", "Still works?")

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        for _ in range(100):
            if self.interaction.has_pending("telegram:1"):
                break
            time.sleep(0.01)
        self.assertTrue(self.interaction.answer("telegram:1", "yes"))
        worker.join(timeout=5)
        self.assertEqual(box["result"], "yes")

    def test_unregister_channel_restores_async_behaviour(self):
        self.interaction.register_channel("cli:local", lambda _e: "inline")
        self.interaction.unregister_channel("cli:local")
        self.assertNotIn("cli:local", self.interaction._CHANNELS)


class ToolExposureTests(AskUserBase):
    def test_ask_user_is_available_in_every_profile(self):
        tools = importlib.import_module("zeline.tools")
        names_by_profile = {
            profile: {d.name for d in tools.TOOL_DEFS if profile in d.profiles}
            for profile in ("safe", "workspace", "full")
        }
        for profile, names in names_by_profile.items():
            with self.subTest(profile=profile):
                self.assertIn("ask_user", names)

    def test_schema_declares_options_as_an_array_of_strings(self):
        tools = importlib.import_module("zeline.tools")
        definition = next(d for d in tools.TOOL_DEFS if d.name == "ask_user")
        options = definition.parameters["properties"]["options"]
        self.assertEqual(options["type"], "array")
        self.assertEqual(options["items"]["type"], "string")
        self.assertEqual(definition.parameters["required"], ["question"])

    def test_executor_routes_ask_user_to_the_interaction_module(self):
        tools = importlib.import_module("zeline.tools")
        interaction = importlib.import_module("zeline.interaction")
        executor = tools.ToolExecutor("telegram:7", profile="safe", workspace=self.home)
        with mock.patch.object(interaction, "ask", return_value="routed") as ask:
            result = executor.run("ask_user", {"question": "Which?", "options": ["a", "b"]})
        self.assertEqual(result, "routed")
        ask.assert_called_once_with("telegram:7", "Which?", ["a", "b"])

    def test_description_tells_the_model_when_not_to_ask(self):
        """Without this the model turns every task into a questionnaire."""
        tools = importlib.import_module("zeline.tools")
        definition = next(d for d in tools.TOOL_DEFS if d.name == "ask_user")
        text = definition.description.casefold()
        self.assertIn("do not use this", text)
        self.assertIn("already clear", text)
        self.assertIn("never re-ask", text)


class SessionIntegrationTests(AskUserBase):
    def test_stop_and_reset_cancel_a_pending_question(self):
        sessions_mod = importlib.import_module("zeline.sessions")
        interaction = importlib.import_module("zeline.interaction")
        store = sessions_mod.SessionStore(persistence=None)

        for label, call in (("stop", store.stop), ("reset", store.reset)):
            with self.subTest(path=label):
                session = mock.Mock()
                session.running = True
                session.cancel_event = threading.Event()
                store._sessions["telegram:5"] = session
                with mock.patch.object(interaction, "cancel", return_value=True) as cancel:
                    call("telegram:5")
                cancel.assert_called_once_with("telegram:5")


if __name__ == "__main__":
    unittest.main()
