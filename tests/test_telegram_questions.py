"""Question cards are persistent evidence, not ephemeral progress bubbles."""
import importlib
import threading
import time
from unittest import mock

from tests.test_ask_user import AskUserBase


class TelegramQuestionTests(AskUserBase):
    def setUp(self):
        super().setUp()
        self.telegram = importlib.import_module("zeline.gateways.telegram")
        self.calls = []
        self.sequence = 100

        def api(_api, method, **payload):
            self.calls.append((method, payload))
            if method == "sendMessage":
                self.sequence += 1
                return {"ok": True, "result": {"message_id": self.sequence}}
            return {"ok": True}

        self.patch = mock.patch.object(self.telegram, "_api_call", side_effect=api)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def start_question(self, options=("Yes", "No"), question="Proceed <safely>?"):
        ready = threading.Event()
        box = {}
        live = self.telegram._LiveStatus("fixture", 1)
        live.turn_started -= 500
        live.add("Reading source")

        def renderer(entry):
            live.set_awaiting_user()
            self.telegram._render_ask_question("fixture", 1, entry)
            ready.set()

        self.interaction.register_channel("telegram:1", renderer)
        worker = threading.Thread(target=lambda: box.update(result=self.interaction.ask(
            "telegram:1", question, options)), daemon=True)
        worker.start()
        self.addCleanup(lambda: (self.interaction.cancel("telegram:1"), worker.join(2)))
        self.assertTrue(ready.wait(2), "question renderer did not complete")
        entry = self.interaction.pending("telegram:1")
        card = next(p for m, p in reversed(self.calls) if m == "sendMessage" and "reply_markup" in p)
        return worker, box, live, entry, card

    def test_waiting_status_is_immediate_and_not_working(self):
        worker, box, live, entry, card = self.start_question()
        self.assertIn("Waiting for your answer", live._render())
        self.assertNotIn("Working", live._render())
        live.tick()
        self.assertIn("Waiting for your answer", self.calls[-1][1]["text"])

    def test_every_question_has_custom_answer_even_without_options(self):
        for options in ((), ("Yes", "No")):
            with self.subTest(options=options):
                entry = self.interaction.PendingQuestion("telegram:1", "Question?", options)
                self.telegram._render_ask_question("fixture", 1, entry)
                payload = self.calls[-1][1]
                labels = [b["text"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]
                self.assertIn("Fill in yourself", labels)
                self.assertIn("Ask user:", payload["text"])

    def test_click_keeps_question_and_chosen_answer_and_duplicate_does_not_erase_it(self):
        worker, box, live, entry, card = self.start_question()
        data = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        message_id = self.sequence
        self.telegram._handle_ask_callback("fixture", 1, message_id, data)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(box["result"], "Yes")
        terminal = [p for m, p in self.calls if m == "editMessageText" and p.get("message_id") == message_id][-1]
        self.assertIn("Ask user:", terminal["text"])
        self.assertIn("Proceed <safely>?", terminal["text"])
        self.assertIn("Answer: Yes", terminal["text"])
        before = len(self.calls)
        self.telegram._handle_ask_callback("fixture", 1, message_id, data)
        self.assertEqual(len(self.calls), before, "old taps must not overwrite the Q&A receipt")

    def test_custom_button_waits_for_real_text_and_receipt_records_it(self):
        worker, box, live, entry, card = self.start_question()
        data = card["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
        self.telegram._handle_ask_callback("fixture", 1, self.sequence, data)
        self.assertFalse(entry.event.is_set())
        self.assertTrue(self.interaction.answer("telegram:1", "My own answer"))
        worker.join(2)
        self.assertEqual(box["result"], "My own answer")
        self.assertTrue(any("Answer: My own answer" in p.get("text", "") for m, p in self.calls))

    def test_direct_text_does_not_need_a_custom_button_click(self):
        worker, box, live, entry, card = self.start_question()
        self.assertTrue(self.interaction.answer("telegram:1", "Typed directly"))
        worker.join(2)
        self.assertEqual(box["result"], "Typed directly")
        self.assertTrue(any("Answer: Typed directly" in p.get("text", "") for m, p in self.calls))

    def test_old_question_and_wrong_chat_cannot_resolve_current_question(self):
        worker, box, live, entry, card = self.start_question()
        data = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.telegram._handle_ask_callback("fixture", 2, self.sequence, data)
        self.assertFalse(entry.event.is_set())
        self.telegram._handle_ask_callback("fixture", 1, self.sequence + 1, data)
        self.assertFalse(entry.event.is_set())
        self.assertLessEqual(len(data.encode()), 64)

    def test_telegram_wait_is_not_expired_by_short_tool_timeout(self):
        with mock.patch.object(self.interaction, "_timeout_seconds", return_value=.03):
            worker, box, live, entry, card = self.start_question()
            time.sleep(.12)
            self.assertTrue(worker.is_alive(), "Telegram picker silently expired")
            self.assertTrue(self.interaction.answer("telegram:1", "late but valid"))
            worker.join(2)
            self.assertEqual(box["result"], "late but valid")

    def test_finalized_progress_cannot_be_resurrected_by_late_heartbeat(self):
        live = self.telegram._LiveStatus("fixture", 1)
        live.turn_started -= 500
        live.add("Reading source")
        live.finalize()
        before = len(self.calls)
        live.tick()
        live.set_waiting()
        live.add("Late tool notification")
        self.assertEqual(len(self.calls), before)
        self.assertNotIn("Working", self.calls[-1][1]["text"])
