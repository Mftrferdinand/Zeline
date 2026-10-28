"""Stop invalidates admitted work; waiting workers must never reset cancellation."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


class TurnAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "ZELINE_HOME": str(Path(self.temporary.name) / "state"),
            "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
            "ZELINE_MODEL": "fixture-model",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in tuple(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.module = importlib.import_module("zeline.sessions")
        self.module.config.PERSIST_SESSIONS = False
        self.store = self.module.SessionStore()
        self.identity = "telegram:fixture"

    def test_telegram_stop_ack_is_prompt_while_shell_cleanup_blocks(self):
        from zeline.gateways import telegram
        ticket = self.store.admit(self.identity, "safe")
        entered = threading.Event()
        release = threading.Event()
        messages = []

        def slow_cleanup(_identity):
            entered.set()
            release.wait(1)
            return 1

        try:
            with mock.patch.object(self.module.tools, "cancel_identity", side_effect=slow_cleanup), \
                 mock.patch.object(telegram, "_api_call", side_effect=lambda _api, _method, **kw: messages.append(kw.get("text"))):
                started = time.monotonic()
                self.assertTrue(telegram._handle_command_update(
                    "fixture", "/stop", self.store, self.identity, 42,
                    stop_event=threading.Event(), tool_profile="safe"))
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, .3, "Telegram ack must not wait for the shell to exit")
                self.assertTrue(ticket.cancel_event.is_set())
                self.assertTrue(entered.wait(.3))
                self.assertEqual(messages, ["⛔ Stopped. You can continue this session."])
        finally:
            release.set()

    def test_fresh_turn_waits_until_stop_cleanup_finishes(self):
        ticket = self.store.admit(self.identity, "safe")
        entered = threading.Event()
        release = threading.Event()
        claimed = []

        def slow_cleanup(_identity):
            entered.set()
            release.wait(2)
            return 1

        with mock.patch.object(self.module.tools, "cancel_identity", side_effect=slow_cleanup):
            try:
                self.assertTrue(self.store.request_stop(self.identity))
                self.assertTrue(entered.wait(.3))
                self.assertTrue(ticket.cancel_event.is_set())
                self.assertFalse(self.store.request_stop(self.identity), "repeat stop must not send a second ack")
                fresh = self.store.admit(self.identity, "safe")
                worker = threading.Thread(target=lambda: claimed.append(self.store.wait_turn(fresh)))
                worker.start()
                self.assertFalse(fresh.started_at, "new turn started before old shell was reaped")
                self.assertEqual(claimed, [])
            finally:
                release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(claimed, [True])
        self.store.finish_turn(fresh)

    def test_request_stop_actually_kills_foreground_shell(self):
        from zeline import tools
        ticket = self.store.admit(self.identity, "safe")
        started = threading.Event()
        output = []

        def run_shell():
            output.append(tools._run_tracked(
                [sys.executable, "-c", "import time;time.sleep(20)"],
                shell=False, cwd=None, seconds=25, identity=self.identity))

        original = tools._fg_track
        def observe(identity, process):
            original(identity, process)
            started.set()

        with mock.patch.object(tools, "_fg_track", side_effect=observe):
            worker = threading.Thread(target=run_shell, daemon=True)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                begin = time.monotonic()
                self.assertTrue(self.store.request_stop(self.identity))
                self.assertLess(time.monotonic() - begin, .3)
                worker.join(4)
                self.assertFalse(worker.is_alive(), "foreground command survived /stop")
                self.assertTrue(ticket.cancel_event.is_set())
                self.assertNotEqual(output[0][0], 0)
            finally:
                tools.cancel_identity(self.identity)
                worker.join(4)
                self.store.finish_turn(ticket)

    def test_stop_invalidates_a_worker_waiting_for_the_session_lock(self):
        session = self.store.get_or_create(self.identity, "safe")
        started = threading.Event()
        release = threading.Event()
        queued = threading.Event()
        calls, replies = [], []
        original = self.store.get_or_create
        def lookup(*args, **kwargs):
            result = original(*args, **kwargs)
            if threading.current_thread().name == "second":
                queued.set()
            return result
        def send(text, **kwargs):
            calls.append(text)
            if text == "first":
                started.set()
                self.assertTrue(release.wait(5))
                return "Stopped." if kwargs["should_stop"]() else "completed first"
            return "completed second"
        with mock.patch.object(session.agent, "send", side_effect=send), \
             mock.patch.object(self.store, "get_or_create", side_effect=lookup):
            first = threading.Thread(target=lambda: replies.append(self.store.send(self.identity, "first", "safe")), name="first")
            second = threading.Thread(target=lambda: replies.append(self.store.send(self.identity, "second", "safe")), name="second")
            first.start()
            self.assertTrue(started.wait(5))
            second.start()
            self.assertTrue(queued.wait(5))
            try:
                self.assertTrue(self.store.stop(self.identity))
            finally:
                release.set()
                first.join(5)
                second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, ["first"], "A queued worker resumed after /stop")
        self.assertEqual(replies, ["Stopped.", "Stopped."])

    def test_reserved_turn_is_stoppable_before_worker_starts_and_new_turn_works(self):
        ticket = self.store.admit(self.identity, "safe")
        self.assertIsNotNone(self.store.progress(self.identity))
        self.assertTrue(self.store.stop(self.identity))
        with mock.patch.object(ticket.session.agent, "send", return_value="fresh answer") as call:
            self.assertEqual(self.store.send(self.identity, "old", "safe", ticket=ticket), "Stopped.")
            self.store.finish_turn(ticket)
            self.assertEqual(self.store.send(self.identity, "fresh", "safe"), "fresh answer")
        self.assertEqual(call.call_count, 1)
        self.assertTrue(ticket.cancel_event.is_set())

    def test_admission_order_is_fifo_even_if_worker_threads_start_in_reverse(self):
        first = self.store.admit(self.identity, "safe")
        second = self.store.admit(self.identity, "safe")
        third = self.store.admit(self.identity, "safe")
        calls, output, errors = [], [], []
        def run(ticket, text):
            try:
                output.append(self.store.send(self.identity, text, "safe", ticket=ticket))
            except Exception as exc:
                errors.append(exc)
            finally:
                self.store.finish_turn(ticket)
        with mock.patch.object(first.session.agent, "send", side_effect=lambda text, **kwargs: calls.append(text) or text):
            workers = [threading.Thread(target=run, args=(ticket, text))
                       for ticket, text in [(third, "third"), (second, "second"), (first, "first")]]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
        self.assertFalse(errors)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(calls, ["first", "second", "third"])
        self.assertEqual(output, calls)

    def test_interrupt_queues_followups_without_cancelling_already_queued_turns(self):
        first = self.store.admit(self.identity, "safe")
        self.assertTrue(self.store.wait_turn(first))
        second = self.store.admit(self.identity, "safe", interrupt=True)
        third = self.store.admit(self.identity, "safe", interrupt=True)
        self.assertEqual(first.reason, "interrupt")
        self.assertTrue(first.cancel_event.is_set())
        self.assertFalse(second.cancel_event.is_set())
        self.assertFalse(third.cancel_event.is_set())
        self.assertIsNotNone(second.previous_progress)
        self.store.finish_turn(first)
        self.assertTrue(self.store.wait_turn(second))
        self.store.finish_turn(second)
        self.assertTrue(self.store.wait_turn(third))
        self.store.finish_turn(third)
        self.assertIsNone(self.store.progress(self.identity))

    def test_telegram_busy_ack_precedes_queued_turn_and_stop_discards_pending(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "safe")
        first_started, release = threading.Event(), threading.Event()
        calls, sent = [], []
        def agent_send(text, **callbacks):
            calls.append(text)
            callbacks["on_iteration"](18, 150)
            if text == "first":
                first_started.set()
                self.assertTrue(release.wait(5))
                return "obsolete answer"
            return "fresh answer: " + text
        def api(_api, method, **kwargs):
            if method == "sendMessage":
                sent.append(kwargs.get("text", ""))
            return {"ok": True, "result": {"message_id": 11}}
        with mock.patch.object(session.agent, "send", side_effect=agent_send), \
             mock.patch.object(telegram, "_api_call", side_effect=api), \
             mock.patch.object(telegram, "_LiveStatus"), \
             mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()):
            first = telegram._start_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                                               text="first", tool_profile="safe")
            self.assertTrue(first_started.wait(5))
            second = telegram._start_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                                                text="second", tool_profile="safe")
            third = telegram._start_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                                               text="third", tool_profile="safe")
            try:
                acknowledgements = [text for text in sent if "Interrupting current task" in text]
                self.assertTrue(acknowledgements, "Busy message received no acknowledgment")
                self.assertIn("iteration 18/150", acknowledgements[0])
                self.assertIn("I'll respond to your message shortly.", acknowledgements[0])
                self.assertEqual(calls, ["first"])
                telegram._handle_command_update("fixture", "/stop", self.store, self.identity, 1,
                                                stop_event=threading.Event(), tool_profile="safe", allowed=[1])
            finally:
                release.set()
                for worker in (first, second, third):
                    worker.join(5)
            self.assertTrue(all(not worker.is_alive() for worker in (first, second, third)))
        self.assertEqual(calls, ["first"])
        self.assertFalse(any("obsolete answer" in text or "fresh answer" in text for text in sent))
        self.assertEqual(sum(text.startswith("⛔ Stopped") for text in sent), 1)

    def test_cancelled_turn_cannot_emit_late_narration_or_progress(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        ticket = self.store.admit(self.identity, "safe")
        self.assertTrue(self.store.wait_turn(ticket))
        sent = []
        def send(text, **callbacks):
            self.store.stop(self.identity)
            callbacks["on_narration"]("late narration must not be sent")
            callbacks["on_tool"]("read_file", {"path": "late.txt"})
            callbacks["on_tool_result"]("read_file", {}, "late result")
            return "late final must not be sent"
        with mock.patch.object(ticket.session.agent, "send", side_effect=send), \
             mock.patch.object(telegram, "_api_call", side_effect=lambda api, method, **kwargs: sent.append((method, kwargs)) or {"ok": True}), \
             mock.patch.object(telegram, "_LiveStatus") as live, \
             mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()):
            telegram._send_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                                       text="fixture", tool_profile="safe", _ticket=ticket)
        self.store.finish_turn(ticket)
        self.assertFalse(any(method == "sendMessage" for method, kwargs in sent))
        live.return_value.finalize.assert_called_once()
        live.return_value.clear.assert_not_called()
        live.return_value.add.assert_not_called()

    def test_reset_invalidates_admitted_work_and_does_not_reuse_the_old_agent(self):
        old = self.store.admit(self.identity, "safe")
        self.assertTrue(self.store.reset(self.identity))
        with mock.patch.object(old.session.agent, "send") as stale:
            self.assertEqual(self.store.send(self.identity, "old", "safe", ticket=old), "Stopped.")
        stale.assert_not_called()
        self.assertTrue(old.cancel_event.is_set())
        self.store.finish_turn(old)
        new = self.store.admit(self.identity, "safe")
        self.assertIsNot(new.session, old.session)
        self.store.release(new)

    def test_busy_turns_produce_fifo_replies_not_obsolete_output(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "safe")
        entered, release = threading.Event(), threading.Event()
        sent, calls = [], []
        def send(text, **callbacks):
            calls.append(text)
            if text == "first":
                entered.set()
                self.assertTrue(release.wait(5))
                return "obsolete first answer"
            return "answer for " + text
        def api(_api, method, **kwargs):
            if method == "sendMessage":
                sent.append(kwargs)
            return {"ok": True, "result": {"message_id": len(sent) + 1}}
        with mock.patch.object(session.agent, "send", side_effect=send), \
             mock.patch.object(telegram, "_api_call", side_effect=api), \
             mock.patch.object(telegram, "_LiveStatus"), \
             mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
             mock.patch.object(self.store, "reflect", return_value=None):
            workers = [telegram._start_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                        text="first", tool_profile="safe", reply_to_message_id=1)]
            self.assertTrue(entered.wait(5))
            workers.extend(telegram._start_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                           text=text, tool_profile="safe", reply_to_message_id=index)
                           for index, text in [(2, "second"), (3, "third")])
            release.set()
            for worker in workers:
                worker.join(5)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
        replies = [row for row in sent if row.get("text", "").startswith("answer for")]
        self.assertEqual([row["text"] for row in replies], ["answer for second", "answer for third"])
        self.assertEqual([row["reply_to_message_id"] for row in replies], [2, 3])
        self.assertFalse(any("obsolete first" in row.get("text", "") for row in sent))
        self.assertEqual(calls, ["first", "second", "third"])

    def test_reflection_never_overtakes_an_admitted_user_message(self):
        ticket = self.store.admit(self.identity, "safe")
        with mock.patch.object(ticket.session.agent, "reflect", return_value="unwanted reflection") as reflect:
            self.assertIsNone(self.store.reflect(self.identity))
        reflect.assert_not_called()
        self.store.release(ticket)

    def test_running_reflection_is_visible_and_cancelled_by_stop(self):
        session = self.store.get_or_create(self.identity, 'safe')
        entered, released = threading.Event(), threading.Event()
        observed = []
        def reflect(**kwargs):
            entered.set()
            self.assertTrue(released.wait(3))
            observed.append(session.agent._cancelled())
            return 'obsolete reflection output'
        results = []
        with mock.patch.object(session.agent, 'reflect', side_effect=reflect):
            worker = threading.Thread(target=lambda: results.append(self.store.reflect(self.identity)))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNotNone(self.store.progress(self.identity))
                self.assertTrue(self.store.stop(self.identity))
            finally:
                released.set()
                worker.join(3)
        self.assertEqual(observed, [True])
        self.assertEqual(results, [None])
        self.assertIsNone(self.store.progress(self.identity))

    def test_media_preparation_does_not_block_poll_or_restart_after_stop(self):
        telegram = importlib.import_module('zeline.gateways.telegram')
        entered, release, dispatched = threading.Event(), threading.Event(), threading.Event()
        sent, workers = [], []
        def download(*args):
            entered.set()
            release.wait(3)
            return None, 'fixture download error'
        real_start = telegram._start_agent_reply
        def start(*args, **kwargs):
            thread = real_start(*args, **kwargs)
            workers.append(thread)
            return thread
        def dispatch():
            telegram._dispatch_update('fixture', 'fixture', self.store,
                {'message': {'chat': {'id': 1}, 'voice': {'file_id': 'fixture'}}},
                allowed=[1], tool_profile='safe', stop_event=threading.Event())
            dispatched.set()
        with mock.patch.object(telegram, '_download_media_file', side_effect=download), \
             mock.patch.object(telegram, '_api_call', side_effect=lambda api, method, **kwargs: sent.append((method, kwargs)) or {'ok': True}), \
             mock.patch.object(telegram, '_start_agent_reply', side_effect=start):
            poll = threading.Thread(target=dispatch, daemon=True)
            poll.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(dispatched.wait(.8), 'Media preprocessing blocks /stop polling')
                self.assertTrue(self.store.stop('telegram:1'))
                for worker in workers:
                    worker.join(.8)
                    self.assertFalse(worker.is_alive())
            finally:
                release.set()
                poll.join(3)
                for worker in workers:
                    worker.join(3)
        self.assertFalse(any(method == 'sendMessage' for method, kwargs in sent))

    def test_telegram_interrupt_status_matches_requested_wording(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        if not hasattr(telegram, "_interrupt_ack"):
            self.fail("No status-rich busy-message acknowledgment")
        self.assertEqual(telegram._interrupt_ack({"elapsed": 660, "iteration": 18, "maximum": 150}),
                         "💥 Interrupting current task (11 min elapsed, iteration 18/150). I'll respond to your message shortly.")


if __name__ == "__main__":
    unittest.main()
