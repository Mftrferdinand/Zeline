"""Learning reports are post-turn messages, never live tool-feed entries."""
import importlib
import threading
import unittest
from unittest import mock

from tests import test_turn_admission as admission


PHASES = ("📙 Self-Enhancement", "📘 Self-Improvement", "📗 Self-Refinement")


class LearningOrderTests(unittest.TestCase):
    setUp = admission.TurnAdmissionTests.setUp

    def test_foreground_learning_waits_for_final_and_is_separate_by_phase(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        paused, release = threading.Event(), threading.Event()
        calls = []
        live = mock.Mock()
        real_thread = threading.Thread

        def thread(*args, **kwargs):
            if kwargs.get("name", "").startswith("zeline-reflect-"):
                return mock.Mock(start=kwargs["target"])
            return real_thread(*args, **kwargs)

        def send(_text, **callbacks):
            for action in ("create", "patch", "write_file"):
                args = {"action": action, "name": "fixture-skill"}
                callbacks["on_tool"]("manage_skill", args)
                callbacks["on_tool_result"]("manage_skill", args, "OK: verified fixture mutation " + action)
            paused.set()
            if not release.wait(3):
                raise AssertionError("fixture task not released")
            return "Final task answer"

        summary = "\n\n".join(phase + " review: observed task evidence." for phase in PHASES)
        with mock.patch.object(session.agent, "send", side_effect=send), \
                mock.patch.object(session.agent, "reflect", return_value=summary), \
                mock.patch.object(telegram, "_api_call", side_effect=lambda api, method, **p: calls.append((method, p)) or {"ok": True}), \
                mock.patch.object(telegram, "_LiveStatus", return_value=live), \
                mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
                mock.patch.object(telegram.threading, "Thread", side_effect=thread):
            worker = telegram._start_agent_reply("fixture", self.store, chat_id=1,
                identity=self.identity, text="Fixture task", tool_profile="full", reply_to_message_id=42)
            try:
                self.assertTrue(paused.wait(3))
                self.assertFalse(any(any(phase in str(c) for phase in PHASES) for c in live.add.call_args_list),
                                 "learning report leaked into the tool feed before task completion")
                self.assertFalse(any(any(phase in p.get("text", "") for phase in PHASES) for _, p in calls))
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        messages = [p for method, p in calls if method == "sendMessage"]
        final = next(i for i, p in enumerate(messages) if p["text"] == "Final task answer")
        for phase in PHASES:
            reports = [(i, p) for i, p in enumerate(messages) if phase in p["text"]]
            self.assertEqual(len(reports), 1, phase)
            i, payload = reports[0]
            self.assertGreater(i, final)
            self.assertNotIn("reply_to_message_id", payload)
            self.assertFalse(any(other in payload["text"] for other in PHASES if other != phase))
            self.assertNotIn("Creating skill", payload["text"])

    def test_failed_final_delivery_does_not_start_learning(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        with mock.patch.object(session.agent, "send", return_value="Final task answer"), \
                mock.patch.object(session.agent, "reflect") as reflect, \
                mock.patch.object(telegram, "_api_call", return_value=None), \
                mock.patch.object(telegram, "_LiveStatus"), \
                mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()):
            completed = telegram._send_agent_reply("fixture", self.store, chat_id=1,
                identity=self.identity, text="Fixture task", tool_profile="full")
        self.assertFalse(completed)
        reflect.assert_not_called()

    def test_later_final_part_failure_prevents_admitted_reflection(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        sent = []

        def api(_api, method, **payload):
            if method == "sendMessage":
                sent.append(payload["text"])
                return None if payload["text"] == "Final part two" else {"ok": True}
            return {"ok": True}

        with mock.patch.object(session.agent, "send", return_value="Final task answer"), \
                mock.patch.object(session.agent, "reflect") as reflect, \
                mock.patch.object(telegram, "_telegram_html_parts", return_value=["Final part one", "Final part two"]), \
                mock.patch.object(telegram, "_api_call", side_effect=api), \
                mock.patch.object(telegram, "_LiveStatus"), \
                mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()):
            worker = telegram._start_agent_reply("fixture", self.store, chat_id=1,
                identity=self.identity, text="Fixture task", tool_profile="full")
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(sent, ["Final part one", "Final part two"])
        self.assertIsNone(self.store.progress(self.identity))
        reflect.assert_not_called()

    def test_cancellation_drops_pending_learning_instead_of_reporting_completion(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        paused, release = threading.Event(), threading.Event()

        def send(_text, **callbacks):
            callbacks["on_tool_result"]("manage_skill", {"action": "create"}, "OK: fixture mutation")
            paused.set()
            release.wait(3)
            return "Stopped."

        with mock.patch.object(session.agent, "send", side_effect=send), \
                mock.patch.object(session.agent, "reflect") as reflect, \
                mock.patch.object(telegram, "_api_call", return_value={"ok": True}) as api, \
                mock.patch.object(telegram, "_LiveStatus"), \
                mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()):
            worker = telegram._start_agent_reply("fixture", self.store, chat_id=1,
                identity=self.identity, text="Fixture task", tool_profile="full")
            try:
                self.assertTrue(paused.wait(3))
                session.cancel_event.set()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        reflect.assert_not_called()
        self.assertFalse(any(any(p in c.kwargs.get("text", "") for p in PHASES)
                             for c in api.call_args_list))

    def test_interrupted_turn_cannot_leak_its_learning_into_the_next_turn(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        paused, release = threading.Event(), threading.Event()
        real_thread = threading.Thread

        def thread(*args, **kwargs):
            if kwargs.get("name", "").startswith("zeline-reflect-"):
                return mock.Mock(start=kwargs["target"])
            return real_thread(*args, **kwargs)

        def send(text, **callbacks):
            if text == "Task A":
                callbacks["on_tool_result"]("manage_skill", {"action": "create"}, "OK: Task A mutation")
                paused.set()
                release.wait(3)
                return "Interrupted old task"
            return "Task B complete"

        with mock.patch.object(session.agent, "send", side_effect=send), \
                mock.patch.object(session.agent, "reflect", return_value=None), \
                mock.patch.object(telegram, "_api_call", return_value={"ok": True}) as api, \
                mock.patch.object(telegram, "_LiveStatus"), \
                mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
                mock.patch.object(telegram.threading, "Thread", side_effect=thread):
            first = telegram._start_agent_reply("fixture", self.store, chat_id=1,
                identity=self.identity, text="Task A", tool_profile="full")
            second = None
            try:
                self.assertTrue(paused.wait(3))
                second = telegram._start_agent_reply("fixture", self.store, chat_id=1,
                    identity=self.identity, text="Task B", tool_profile="full")
            finally:
                release.set()
                first.join(5)
                if second:
                    second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        texts = [c.kwargs.get("text", "") for c in api.call_args_list if c.args[1] == "sendMessage"]
        self.assertIn("Task B complete", texts)
        self.assertFalse(any(any(phase in text for phase in PHASES) for text in texts))

    def test_phase_body_paragraphs_are_not_emitted_as_unlabelled_bubbles(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        summary = PHASES[0] + " review: evidence.\n\nReason and limitations.\n\n" + PHASES[1] + " review: proposal only."
        parts = list(telegram._learning_report_parts((), summary))
        self.assertEqual(len(parts), 2)
        self.assertIn("Reason and limitations.", parts[0])
        self.assertNotIn(PHASES[1], parts[0])

    def test_long_escaped_unicode_learning_keeps_all_evidence_and_valid_parts(self):
        import html
        telegram = importlib.import_module("zeline.gateways.telegram")
        evidence = "😀<literal>&" * 1000
        summary = "📙 <b>Self-Enhancement:</b> " + html.escape(evidence)
        parts = list(telegram._learning_report_parts((), summary))
        self.assertGreater(len(parts), 1)
        reconstructed = ""
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-16-le")) // 2, 3500)
            reconstructed += html.unescape(part).removeprefix(PHASES[0] + "\n")
        self.assertEqual(reconstructed, "📙 Self-Enhancement: " + evidence)

    def test_post_review_cancellation_prevents_report_delivery(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        cancelled = threading.Event()
        sessions = mock.Mock()

        def reflect(_identity):
            cancelled.set()
            return PHASES[0] + " review: complete"

        sessions.reflect.side_effect = reflect
        with mock.patch.object(telegram.threading, "Thread", side_effect=lambda **kw: mock.Mock(start=kw["target"])), \
                mock.patch.object(telegram, "_api_call") as api:
            telegram._start_reflection("fixture", sessions, 1, self.identity,
                learning_updates=(PHASES[0] + ": real mutation",), cancelled=cancelled.is_set)
        sessions.reflect.assert_called_once()
        api.assert_not_called()

    def test_stop_during_real_session_reflection_does_not_flush_foreground_updates(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        foreground = self.store.admit(self.identity, "full")
        self.assertTrue(self.store.wait_turn(foreground))
        self.store.finish_turn(foreground)

        def stop_reflection(**_kwargs):
            self.store.stop(self.identity)
            return PHASES[0] + " review: must not be shown"

        with mock.patch.object(session.agent, "reflect", side_effect=stop_reflection), \
                mock.patch.object(telegram.threading, "Thread", side_effect=lambda **kw: mock.Mock(start=kw["target"])), \
                mock.patch.object(telegram, "_api_call") as api:
            telegram._start_reflection("fixture", self.store, 1, self.identity,
                learning_updates=(PHASES[0] + ": real mutation",), cancelled=foreground.cancel_event.is_set)
        self.assertFalse(foreground.cancel_event.is_set())
        self.assertTrue(session.cancel_event.is_set())
        api.assert_not_called()

    def test_review_error_still_reports_real_mutations_but_not_invented_secondary_phases(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        sessions = mock.Mock()
        sessions.reflect.side_effect = RuntimeError("fixture provider failure")
        with mock.patch.object(telegram.threading, "Thread", side_effect=lambda **kw: mock.Mock(start=kw["target"])), \
                mock.patch.object(telegram, "_api_call", return_value={"ok": True}) as api:
            telegram._start_reflection("fixture", sessions, 1, self.identity,
                learning_updates=(PHASES[0] + ": real fixture mutation",))
        payloads = [c.kwargs["text"] for c in api.call_args_list]
        self.assertEqual(payloads, [PHASES[0] + ": real fixture mutation"])
