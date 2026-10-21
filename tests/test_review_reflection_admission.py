"""Typing belongs to an eligible, admitted reflection, never its scheduling."""
import importlib
import threading
import unittest
from unittest import mock

from tests import test_agent as support


class ReflectionAdmissionTests(unittest.TestCase):
    setUp = support.AgentLoopTests.setUp
    tearDown = support.AgentLoopTests.tearDown

    def setup_review(self, profile="full", calls=5):
        self.sessions = importlib.import_module("zeline.sessions")
        self.telegram = importlib.import_module("zeline.gateways.telegram")
        self.store = self.sessions.SessionStore()
        self.identity = "telegram:reflection-admission"
        self.session = self.store.get_or_create(self.identity, profile)
        self.session.agent._tool_calls_since_reflection = calls
        self.session.agent.last_turn_tool_calls = 0
        self.workers = []
        self.actions = []
        owner = self

        class DeterministicThread:
            def __init__(self, target, **kwargs):
                self.target = target
                self.name = kwargs.get("name", "")
                owner.workers.append(self)

            def start(self):
                if self.name.startswith("zeline-reflect-"):
                    self.target()
                # Typing is explicitly advanced by tests: no sleeps/races.

        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.telegram.threading, "Thread", DeterministicThread).start()
        mock.patch.object(self.telegram, "_api_call", side_effect=lambda *a, **kw: self.actions.append((a, kw))).start()
        mock.patch.object(self.session.agent, "_call_llm", return_value={"content": "NO_ACTION"}).start()

    def start_review(self):
        self.telegram._start_reflection("api", self.store, 123, self.identity)

    def assert_no_typing(self):
        self.assertEqual([w for w in self.workers if not w.name.startswith("zeline-reflect-")], [])
        self.assertEqual(self.actions, [])

    def test_lightweight_review_never_creates_typing_worker(self):
        self.setup_review(calls=4)
        self.start_review()
        self.assert_no_typing()
        self.assertEqual(self.session.agent._tool_calls_since_reflection, 4)
        self.session.agent._call_llm.assert_not_called()

    def test_safe_profile_never_creates_typing_worker(self):
        self.setup_review(profile="safe", calls=100)
        self.start_review()
        self.assert_no_typing()
        self.session.agent._call_llm.assert_not_called()

    def test_busy_or_rejected_review_never_creates_typing_worker(self):
        for state in ("running", "pending", "cancelled", "locked", "missing"):
            with self.subTest(state=state):
                self.setup_review()
                if state == "running":
                    self.session.running = True
                elif state == "pending":
                    self.session.pending_turns.append(self.sessions.TurnTicket(self.identity, self.session))
                elif state == "cancelled":
                    self.session.cancel_event.set()
                elif state == "locked":
                    self.session.lock.acquire()
                else:
                    self.store._sessions.clear()
                try:
                    self.start_review()
                    self.assert_no_typing()
                    self.session.agent._call_llm.assert_not_called()
                finally:
                    if state == "locked":
                        self.session.lock.release()

    def test_eligible_typing_starts_with_owned_ticket_and_obeys_its_cancellation(self):
        self.setup_review()
        observed = []

        def review():
            ticket = self.session.active_turn
            typers = [w for w in self.workers if not w.name.startswith("zeline-reflect-")]
            self.assertEqual(len(typers), 1)
            self.assertIsNotNone(ticket)
            self.assertTrue(self.session.running)
            # One deterministic typing iteration, then no waiting.
            with mock.patch.object(threading.Event, "wait", side_effect=lambda *a: ticket.cancel("stop")):
                typers[0].target()
            self.assertEqual(len(self.actions), 1)
            self.assertEqual(self.actions[0][0][1], "sendChatAction")
            # A future session token must not revive this worker.
            self.session.cancel_event = threading.Event()
            typers[0].target()
            self.assertEqual(len(self.actions), 1)
            observed.append(ticket)
            return {"content": "NO_ACTION"}

        self.session.agent._call_llm.side_effect = review
        self.start_review()
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0].finished.is_set())
        self.assertFalse(self.session.running)
        self.assertIsNone(self.session.active_turn)

    def test_on_ticket_exception_already_releases_admission_and_hooks(self):
        self.setup_review()
        agent_check = self.session.agent._should_stop
        executor_check = getattr(self.session.agent.executor, "_should_stop", None)
        tickets = []

        def fail(ticket):
            tickets.append(ticket)
            raise RuntimeError("observer failed")

        with self.assertRaisesRegex(RuntimeError, "observer failed"):
            self.store.reflect(self.identity, on_ticket=fail)
        self.assertTrue(tickets[0].finished.is_set())
        self.assertFalse(self.session.running)
        self.assertIsNone(self.session.active_turn)
        self.assertIs(self.session.agent._should_stop, agent_check)
        self.assertIs(self.session.agent.executor._should_stop, executor_check)
        self.assertTrue(self.session.lock.acquire(blocking=False))
        self.session.lock.release()

    def test_legacy_noop_adapter_cannot_start_unowned_typing(self):
        self.setup_review()

        class LegacyAdapter:
            def reflect(self, identity):
                return None

        self.telegram._start_reflection("api", LegacyAdapter(), 123, self.identity)
        self.assert_no_typing()

    def test_last_turn_and_custom_threshold_use_the_real_agent_gate(self):
        self.setup_review(calls=0)
        self.session.agent.last_turn_tool_calls = 7
        events = []
        self.assertIsNone(self.store.reflect(self.identity, min_tool_calls=8, on_review=lambda ticket: events.append(ticket)))
        self.assertEqual(events, [])
        self.store.reflect(self.identity, min_tool_calls=7, on_review=lambda ticket: events.append(ticket))
        self.assertEqual(len(events), 1)
        self.assertEqual(self.session.agent.last_turn_tool_calls, 0)
        self.assertEqual(self.session.agent._tool_calls_since_reflection, 0)
        self.session.agent._call_llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
