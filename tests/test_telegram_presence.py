"""Exercise presence through the real Telegram reply path, not a spare helper."""
import importlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


class TelegramPresenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zeline-presence-test-")
        self.addCleanup(self.temp.cleanup)
        self.environment = mock.patch.dict(os.environ, {
            "ZELINE_HOME": str(Path(self.temp.name) / "state"),
            "ZELINE_API_KEY": "offline-fixture", "ZELINE_MODEL": "offline-model",
            "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for name in tuple(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.telegram = importlib.import_module("zeline.gateways.telegram")
        self.network = mock.patch.object(socket.socket, "connect", side_effect=AssertionError("External network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_silent_provider_keeps_typing_without_another_user_message(self):
        calls = []
        lock = threading.Lock()

        def api(_api, method, **params):
            with lock:
                calls.append((time.monotonic(), method, params))
            return {"ok": True, "result": {"message_id": 17}}

        class SilentProvider:
            def send(self, **_kwargs):
                time.sleep(.22)
                return "Verified offline result."

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_start_reflection"), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .04, create=True):
            delivered = self.telegram._send_agent_reply(
                "offline", SilentProvider(), chat_id=1, identity="presence:silent",
                text="Do the task", tool_profile="safe")
        self.assertTrue(delivered)
        typing = [at for at, method, _params in calls if method == "sendChatAction"]
        self.assertGreaterEqual(len(typing), 3,
                                "Real reply path sent typing once, then looked dead during reasoning")
        self.assertLess(max(b - a for a, b in zip(typing, typing[1:])), .16)

    def test_typing_continues_while_final_delivery_waits_for_ack(self):
        sending = threading.Event()
        refreshed = threading.Event()

        def api(_api, method, **params):
            if method == "sendChatAction" and sending.is_set():
                refreshed.set()
            if method == "sendMessage":
                sending.set()
                refreshed.wait(.24)
            return {"ok": True, "result": {"message_id": 18}}

        class InstantProvider:
            def send(self, **_kwargs):
                return "Verified offline final."

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_start_reflection"), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .03):
            delivered = self.telegram._send_agent_reply(
                "offline", InstantProvider(), chat_id=1, identity="presence:delivery",
                text="Do the task", tool_profile="safe")
        self.assertTrue(delivered)
        self.assertTrue(refreshed.is_set(), "Presence stopped before the final was actually delivered")

    def test_typing_refresh_does_not_wait_for_a_blocked_progress_edit(self):
        blocked = threading.Event()
        refreshed = threading.Event()
        foreground_done = threading.Event()

        def api(_api, method, **params):
            if method == "sendChatAction" and blocked.is_set():
                refreshed.set()
            if method == "sendMessage" and ("Executing command" in params.get("text", "") or "Run command" in params.get("text", "")):
                blocked.set()
                refreshed.wait(.35)
            return {"ok": True, "result": {"message_id": 19}}

        class ToolProvider:
            def send(self, **kwargs):
                kwargs["on_tool"]("run_shell", {"command": "offline-fixture"})
                foreground_done.set()
                blocked.wait(.3)
                refreshed.wait(.3)
                return "Verified tool result."

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_start_reflection"), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .03):
            delivered = self.telegram._send_agent_reply(
                "offline", ToolProvider(), chat_id=1, identity="presence:progress",
                text="Do the task", tool_profile="safe")
        self.assertTrue(delivered)
        self.assertTrue(foreground_done.is_set())
        self.assertTrue(refreshed.is_set(), "Progress delivery stole the presence lane")

    def test_receipt_creation_error_cannot_leave_an_idle_typing_worker(self):
        from zeline import telegram_delivery
        calls = []

        def api(_api, method, **params):
            calls.append(method)
            return {"ok": True, "result": {"message_id": 20}}

        class InstantProvider:
            def send(self, **_kwargs):
                return "Verified result."

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .03), \
                mock.patch.object(telegram_delivery.FinalReceipt, "save", side_effect=OSError("fixture disk error")):
            delivered = self.telegram._send_agent_reply(
                "offline", InstantProvider(), chat_id=1, identity="presence:disk",
                text="Do the task", tool_profile="safe")
            self.assertIs(delivered, False)
            self.assertIn("sendMessage", calls, "Disk failure must not silently swallow the result")
            count = calls.count("sendChatAction")

            time.sleep(.12)
            self.assertEqual(calls.count("sendChatAction"), count)

    def test_slow_typing_http_cannot_outlive_its_absolute_timeout(self):
        release = threading.Event()
        entered = threading.Event()

        class Response:
            ok = True
            def json(self):
                return {"ok": True, "result": True}
            def close(self):
                pass

        def stalled_post(*_args, **_kwargs):
            entered.set()
            release.wait(.7)
            return Response()

        try:
            with mock.patch.object(self.telegram._HTTP, "post", side_effect=stalled_post):
                start = time.monotonic()
                payload = self.telegram._api_call("offline", "sendChatAction", timeout=.05,
                                                  attempts=1, chat_id=1, action="typing")
                elapsed = time.monotonic() - start
            self.assertTrue(entered.is_set())
            self.assertIsNone(payload)
            self.assertLess(elapsed, .25, "Socket inactivity timeout was mistaken for a wall-clock bound")
        finally:
            release.set()

    def test_cancelled_turn_stops_typing_and_cannot_deliver_an_old_final(self):
        from types import SimpleNamespace
        cancelled = threading.Event()
        refreshed = threading.Event()
        count = []
        finals = []

        def api(_api, method, **params):
            if method == "sendChatAction":
                count.append(time.monotonic())
                if len(count) >= 2:
                    refreshed.set()
            if method == "sendMessage":
                finals.append(params.get("text", ""))
            return {"ok": True, "result": {"message_id": 21}}

        class Provider:
            def send(self, **kwargs):
                if not refreshed.wait(.3):
                    raise AssertionError("Heartbeat failed before cancellation")
                cancelled.set()
                before = len(count)
                time.sleep(.1)
                self.count_after_cancel = len(count) - before
                return "Obsolete result must not appear."

        provider = Provider()
        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_start_reflection") as reflection, \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .02):
            delivered = self.telegram._send_agent_reply(
                "offline", provider, chat_id=1, identity="presence:stop", text="Do the task",
                tool_profile="safe", _ticket=SimpleNamespace(cancel_event=cancelled, task_id=None))
        self.assertIsNone(delivered)
        self.assertEqual(provider.count_after_cancel, 0)
        self.assertEqual(finals, [])
        reflection.assert_not_called()

    def test_human_question_pauses_typing_until_real_work_resumes(self):
        from zeline import interaction
        from types import SimpleNamespace
        count = []
        paused_counts = []
        resuming = threading.Event()
        resumed = threading.Event()
        resume_baseline = []

        def api(_api, method, **params):
            if method == "sendChatAction":
                count.append(time.monotonic())
                if resuming.is_set() and len(count) >= resume_baseline[0] + 2:
                    resumed.set()
            return {"ok": True, "result": {"message_id": 22}}

        class Provider:
            def send(self, **kwargs):
                interaction._CHANNELS["presence:question"](SimpleNamespace())
                time.sleep(.04)
                before = len(count)
                time.sleep(.1)
                paused_counts.append(len(count) - before)
                resume_baseline.append(len(count))
                resuming.set()
                kwargs["on_tool_result"]("ask_user", {}, "User answered")
                if not resumed.wait(1):
                    raise AssertionError("Typing did not resume after the human answer")
                paused_counts.append(len(count) - before)
                return "Answer received, task complete."

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_render_ask_question"), \
                mock.patch.object(self.telegram, "_start_reflection"), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .02):
            delivered = self.telegram._send_agent_reply(
                "offline", Provider(), chat_id=1, identity="presence:question",
                text="Ask then continue", tool_profile="safe")
        self.assertTrue(delivered)
        self.assertEqual(paused_counts[0], 0, "Human idle time is not working")
        self.assertGreaterEqual(paused_counts[1], 2)

    def test_typing_resumes_after_answer_even_when_status_queue_is_blocked(self):
        from zeline import interaction
        from types import SimpleNamespace
        status_blocked = threading.Event()
        resume_seen = threading.Event()
        resumed_before_release = []
        release = threading.Event()

        def api(_api, method, **params):
            if method == "sendChatAction" and status_blocked.is_set():
                resume_seen.set()
            return {"ok": True, "result": {"message_id": 23}}

        real_waiting = self.telegram._LiveStatus.set_waiting
        def blocked_waiting(status):
            status_blocked.set()
            release.wait(.3)
            real_waiting(status)

        class Provider:
            def send(self, **kwargs):
                interaction._CHANNELS["presence:resume"](SimpleNamespace())
                kwargs["on_tool_result"]("ask_user", {}, "User answered")
                status_blocked.wait(.3)
                resumed_before_release.append(resume_seen.wait(.15))
                release.set()
                return "Resumed and complete."

        try:
            with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                    mock.patch.object(self.telegram, "_render_ask_question"), \
                    mock.patch.object(self.telegram._LiveStatus, "set_waiting", blocked_waiting), \
                    mock.patch.object(self.telegram, "_start_reflection"), \
                    mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .02):
                self.assertTrue(self.telegram._send_agent_reply(
                    "offline", Provider(), chat_id=1, identity="presence:resume",
                    text="Ask then resume", tool_profile="safe"))
            self.assertEqual(resumed_before_release, [True], "A blocked UI job kept working presence paused")
        finally:
            release.set()

    def test_media_preparation_keeps_typing_before_provider_dispatch(self):
        from zeline.sessions import SessionStore
        self.telegram.config.PERSIST_SESSIONS = False
        store = SessionStore()
        store.get_or_create("presence:media", "safe")
        preparing = threading.Event()
        refreshed = threading.Event()
        calls = []

        def api(_api, method, **params):
            calls.append(method)
            if method == "sendChatAction" and preparing.is_set():
                refreshed.set()
            return {"ok": True, "result": {"message_id": 24}}

        def prepare():
            preparing.set()
            refreshed.wait(.2)
            return "", "Offline media fixture error."

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .02):
            worker = self.telegram._start_agent_reply(
                "offline", store, chat_id=1, identity="presence:media",
                text="", tool_profile="safe", prepare=prepare)
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(refreshed.is_set(), "Preparation was active but Telegram looked idle")

    def test_cold_admission_has_presence_before_agent_dispatch(self):
        from zeline.sessions import SessionStore
        self.telegram.config.PERSIST_SESSIONS = False
        entered = threading.Event()
        typed = threading.Event()

        class Persistence:
            def load(self, identity):
                entered.set()
                typed.wait(.2)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = SessionStore(persistence=Persistence())
        def api(_api, method, **params):
            if method == "sendChatAction" and entered.is_set():
                typed.set()
            return {"ok": True, "result": {"message_id": 25}}

        with mock.patch.object(self.telegram, "_api_call", side_effect=api), \
                mock.patch.object(self.telegram, "_send_agent_reply", return_value=False), \
                mock.patch.object(self.telegram, "_TYPING_INTERVAL_SECONDS", .02):
            worker = self.telegram._start_agent_reply(
                "offline", store, chat_id=1, identity="presence:cold",
                text="Hello", tool_profile="safe")
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(typed.is_set(), "Cold restore looked idle before provider dispatch")

    def test_cold_admission_cannot_block_polling_for_stop(self):
        from zeline.sessions import SessionStore
        self.telegram.config.PERSIST_SESSIONS = False
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        workers = []

        class Persistence:
            def load(self, identity):
                entered.set()
                release.wait(1)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = SessionStore(persistence=Persistence())
        def start():
            workers.append(self.telegram._start_agent_reply(
                "offline", store, chat_id=1, identity="presence:poll",
                text="Hello", tool_profile="safe"))
            returned.set()

        with mock.patch.object(self.telegram, "_api_call", return_value={"ok": True, "result": True}), \
                mock.patch.object(self.telegram, "_send_agent_reply", return_value=False):
            dispatcher = threading.Thread(target=start, daemon=True)
            dispatcher.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(returned.wait(.15), "Cold restore blocked incoming /stop polling")
                store.request_stop("presence:poll")
            finally:
                release.set()
                dispatcher.join(2)
                for worker in workers:
                    worker.join(2)


if __name__ == "__main__":
    unittest.main()
