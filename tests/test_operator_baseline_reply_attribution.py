"""Telegram replies are anchored to the triggering turn, not the latest message."""
import importlib
import threading
import unittest
from unittest import mock

from tests import test_turn_admission as admission


class ReplyAttributionTests(unittest.TestCase):
    setUp = admission.TurnAdmissionTests.setUp

    def test_interruption_ack_and_narration_quote_their_own_messages(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "safe")
        entered, release = threading.Event(), threading.Event()
        sent, errors = [], []

        def send(text, **callbacks):
            callbacks["on_narration"]("Narration for " + text)
            if text == "first":
                entered.set()
                if not release.wait(5):
                    errors.append("first worker was not released")
                return "obsolete first answer"
            return "Answer for " + text

        def api(_api, method, **payload):
            if method == "sendMessage":
                sent.append(payload)
            return {"ok": True, "result": {"message_id": len(sent) + 1}}

        workers = []
        with mock.patch.object(session.agent, "send", side_effect=send), \
             mock.patch.object(telegram, "_api_call", side_effect=api), \
             mock.patch.object(telegram, "_LiveStatus"), \
             mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
             mock.patch.object(self.store, "reflect", return_value=None):
            try:
                workers.append(telegram._start_agent_reply("fixture", self.store,
                    chat_id=1, identity=self.identity, text="first", tool_profile="safe", reply_to_message_id=101))
                self.assertTrue(entered.wait(5))
                for message_id, text in ((202, "second"), (303, "third")):
                    workers.append(telegram._start_agent_reply("fixture", self.store,
                        chat_id=1, identity=self.identity, text=text, tool_profile="safe", reply_to_message_id=message_id))
            finally:
                release.set()
                for worker in workers:
                    worker.join(5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertFalse(errors)
        acknowledgements = [p for p in sent if "Interrupting current task" in p["text"]]
        self.assertTrue(acknowledgements)
        self.assertEqual(acknowledgements[0].get("reply_to_message_id"), 202)
        expected = {"Answer for second": 202, "Answer for third": 303}
        for text, target in expected.items():
            with self.subTest(text=text):
                payload = next(p for p in sent if p["text"] == text)
                self.assertEqual(payload.get("reply_to_message_id"), target)
                self.assertTrue(payload.get("allow_sending_without_reply"))
        narrations = [p for p in sent if p["text"].startswith("Narration for")]
        self.assertTrue(narrations)
        self.assertTrue(all("reply_to_message_id" not in p for p in narrations))
        self.assertFalse(any(p["text"] == "obsolete first answer" for p in sent))

    def test_single_turn_narration_and_final_quote_original_without_interrupt(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "safe")

        def send(text, **callbacks):
            callbacks["on_narration"]("Checking your request")
            return "Finished your request"

        with mock.patch.object(session.agent, "send", side_effect=send), \
             mock.patch.object(telegram, "_api_call", return_value={"ok": True}) as api, \
             mock.patch.object(telegram, "_LiveStatus"), \
             mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
             mock.patch.object(self.store, "reflect", return_value=None):
            telegram._send_agent_reply("fixture", self.store, chat_id=1, identity=self.identity,
                text="request", tool_profile="safe", reply_to_message_id=404)
        messages = [c.kwargs for c in api.call_args_list if c.args[1] == "sendMessage"]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("reply_to_message_id", messages[0])
        self.assertEqual(messages[1].get("reply_to_message_id"), 404)