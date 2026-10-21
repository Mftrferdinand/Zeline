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

    def test_failed_terminal_edit_retires_same_keyboard_without_replacement(self):
        from types import SimpleNamespace
        for status in ("answered", "cancelled", "expired"):
            with self.subTest(status=status):
                entry = SimpleNamespace(message_id=42, question="Choose?", options=("Full A", "Full B"),
                                        status=status, answer="Full B")
                with mock.patch.object(self.telegram, "_api_call", return_value=None) as api:
                    self.telegram._ask_edit_receipt("fixture", 1, entry)
                self.assertEqual([call.args[1] for call in api.call_args_list],
                                 ["editMessageText", "editMessageReplyMarkup"])
                cleanup = api.call_args_list[-1].kwargs
                self.assertEqual(cleanup["message_id"], 42)
                self.assertEqual(cleanup["reply_markup"], {"inline_keyboard": []})
                self.assertEqual(cleanup["attempts"], self.telegram._PROGRESS_ATTEMPTS)

    def test_failed_reconciliation_after_delayed_custom_edit_retires_keyboard(self):
        worker, box, _live, entry, card = self.start_question()
        entered, release = threading.Event(), threading.Event()
        completed = []

        def api(_api, method, **payload):
            if method == "editMessageText" and "Type your own answer below." in payload.get("text", ""):
                entered.set()
                release.wait(3)
                completed.append((method, payload))
                return {"ok": True}
            completed.append((method, payload))
            return None if method == "editMessageText" else {"ok": True}

        custom = card["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
        with mock.patch.object(self.telegram, "_api_call", side_effect=api):
            mode = threading.Thread(target=self.telegram._handle_ask_callback,
                args=("fixture", 1, entry.message_id, custom), daemon=True)
            mode.start()
            try:
                self.assertTrue(entered.wait(2))
                self.interaction.answer("telegram:1", "Full typed answer")
                worker.join(2)
                self.assertFalse(worker.is_alive())
            finally:
                release.set()
                mode.join(3)
        self.assertFalse(mode.is_alive())
        self.assertEqual(box["result"], "Full typed answer")
        self.assertEqual(completed[-1][0], "editMessageReplyMarkup")
        self.assertEqual(completed[-1][1]["message_id"], entry.message_id)
        self.assertEqual(completed[-1][1]["reply_markup"], {"inline_keyboard": []})
        self.assertFalse(any(method == "sendMessage" for method, _ in completed))

    def test_letter_picker_shows_full_choices_and_b_returns_original_text(self):
        options = tuple(f"Choice {letter}: " + ("Explain <literally> & carefully. " * 8).strip() for letter in "ABC")
        question = "Which complete approach <works> & why?"
        worker, box, live, entry, card = self.start_question(options, question)
        buttons = [button for row in card["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual([button["text"] for button in buttons], ["A", "B", "C", "Fill in yourself"])
        self.assertEqual(entry.options, options)
        self.assertIn(question, card["text"])
        for letter, option in zip("ABC", options):
            self.assertIn(f"{letter}. {option}", card["text"])
        self.assertNotIn("parse_mode", card)
        self.telegram._handle_ask_callback("fixture", 1, entry.message_id, buttons[1]["callback_data"])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(box["result"], options[1])
        receipt = self.calls[-1][1]
        self.assertIn(f"Answer: {options[1]}", receipt["text"])
        for option in options:
            self.assertIn(option, receipt["text"])
        self.assertEqual(receipt["reply_markup"], {"inline_keyboard": []})

    def test_multipart_choices_keep_one_owned_keyboard_and_complete_receipt(self):
        options = tuple(letter + "😀" * 1999 for letter in "ABCDEF")
        worker, box, live, entry, card = self.start_question(options, "Full <question> & choices?")
        sends = [p for m, p in self.calls if m == "sendMessage" and not p.get("parse_mode")]
        self.assertGreater(len(sends), 1)
        self.assertEqual(sum("reply_markup" in p for p in sends), 1)
        self.assertEqual(entry.message_id, self.sequence)
        combined = "".join(p["text"] for p in sends)
        self.assertIn(entry.question, combined)
        for option in options:
            self.assertIn(option, combined)
        self.assertEqual([r[0]["text"] for r in card["reply_markup"]["inline_keyboard"]],
                         ["A", "B", "C", "D", "E", "F", "Fill in yourself"])
        self.telegram._handle_ask_callback("fixture", 1, entry.message_id,
                                         card["reply_markup"]["inline_keyboard"][1][0]["callback_data"])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(box["result"], options[1])
        receipt = self.calls[-1][1]
        self.assertEqual(receipt["message_id"], entry.message_id)
        self.assertIn(f"Answer: {options[1]}", receipt["text"])
        self.assertEqual(receipt["reply_markup"], {"inline_keyboard": []})
        for method, payload in self.calls:
            if "text" in payload and not payload.get("parse_mode"):
                self.assertLessEqual(len(payload["text"].encode("utf-16-le")) // 2, 4096)

    def test_choices_retain_explanations_but_still_have_finite_limits(self):
        raw = [str(index) + "x" * 3000 for index in range(8)]
        options = self.interaction.normalize_options(raw)
        self.assertEqual(len(options), 6)
        self.assertTrue(all(len(option) == 2000 for option in options))

    def test_long_direct_answer_preserves_context_without_replacing_owned_card(self):
        worker, box, live, entry, card = self.start_question(("A" * 900, "B" * 900))
        self.assertIn(entry.options[0], card["text"])
        owned = entry.message_id
        before = len(self.calls)
        answer = "😀" * 2000
        self.assertTrue(self.interaction.answer("telegram:1", answer))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(box["result"], answer)
        later = self.calls[before:]
        context = "".join(p["text"] for method, p in later if method == "sendMessage")
        self.assertIn(entry.question, context)
        for option in entry.options:
            self.assertIn(option, context)
        self.assertTrue(all("reply_markup" not in p for method, p in later if method == "sendMessage"))
        receipt = later[-1][1]
        self.assertEqual(receipt["message_id"], owned)
        self.assertIn(f"Answer: {answer}", receipt["text"])
        self.assertLessEqual(len(receipt["text"].encode("utf-16-le")) // 2, 4096)
        self.assertEqual(receipt["reply_markup"], {"inline_keyboard": []})

    def test_multipart_delivery_failure_never_creates_a_picker(self):
        entry = self.interaction.PendingQuestion("telegram:1", "Question?", ("x" * 2000,) * 3)
        original_api = self.telegram._api_call.side_effect

        def fail_context(api, method, **payload):
            original_api(api, method, **payload)
            return None

        with mock.patch.object(self.telegram, "_api_call", side_effect=fail_context):
            self.telegram._render_ask_question("fixture", 1, entry)
        self.assertIsNone(entry.message_id)
        self.assertFalse(entry.wait_forever)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("reply_markup", self.calls[0][1])

    def test_initial_multipart_send_race_settles_original_owned_card(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                entry = self.interaction.PendingQuestion("telegram:1", "Question?", ("x" * 2000,) * 3)
                with self.interaction._LOCK:
                    self.interaction._PENDING["telegram:1"] = entry
                entered, release = threading.Event(), threading.Event()
                original_api = self.telegram._api_call.side_effect

                def delayed(api, method, **payload):
                    if method == "sendMessage" and "reply_markup" in payload:
                        entered.set()
                        self.assertTrue(release.wait(2))
                    return original_api(api, method, **payload)

                with mock.patch.object(self.telegram, "_api_call", side_effect=delayed):
                    renderer = threading.Thread(target=self.telegram._render_ask_question,
                                                args=("fixture", 1, entry), daemon=True)
                    renderer.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        if cancelled:
                            self.assertTrue(self.interaction.cancel("telegram:1"))
                        else:
                            self.assertTrue(self.interaction.answer("telegram:1", "During send"))
                        entry.on_resolved(entry)
                    finally:
                        release.set()
                        renderer.join(2)
                self.assertFalse(renderer.is_alive())
                receipt = self.calls[-1][1]
                self.assertEqual(receipt["message_id"], entry.message_id)
                self.assertIn("Answer: (cancelled)" if cancelled else "Answer: During send", receipt["text"])
                self.assertEqual(receipt["reply_markup"], {"inline_keyboard": []})

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

    def test_terminal_receipt_wins_after_delayed_custom_edit(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                worker, box, live, entry, card = self.start_question()
                data = card["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
                entered = threading.Event()
                release = threading.Event()
                errors = []
                original_api = self.telegram._api_call.side_effect

                def delayed_api(api, method, **payload):
                    if method == "editMessageText" and "Type your own answer below." in payload.get("text", ""):
                        entered.set()
                        if not release.wait(2):
                            raise AssertionError("custom edit was not released")
                    # Record completion order, not request-start order: this is
                    # the card Telegram would actually leave visible.
                    return original_api(api, method, **payload)

                def custom_tap():
                    try:
                        self.telegram._handle_ask_callback("fixture", 1, entry.message_id, data)
                    except Exception as exc:
                        errors.append(exc)

                with mock.patch.object(self.telegram, "_api_call", side_effect=delayed_api):
                    callback = threading.Thread(target=custom_tap, daemon=True)
                    callback.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        if cancelled:
                            self.assertTrue(self.interaction.cancel("telegram:1"))
                        else:
                            self.assertTrue(self.interaction.answer("telegram:1", "Typed during edit"))
                        worker.join(1)
                        self.assertFalse(worker.is_alive(), "resolution must not wait for the custom edit")
                        receipt = "Answer: (cancelled)" if cancelled else "Answer: Typed during edit"
                        self.assertIn(receipt, self.calls[-1][1]["text"])
                        self.assertIn("CANCELLED" if cancelled else "Typed during edit", box["result"])
                    finally:
                        release.set()
                        callback.join(2)
                    self.assertFalse(callback.is_alive())
                    self.assertEqual(errors, [])
                edits = [p for m, p in self.calls if m == "editMessageText" and p.get("message_id") == entry.message_id]
                self.assertIn(receipt, edits[-1]["text"])
                self.assertEqual(edits[-1]["reply_markup"], {"inline_keyboard": []})

    def test_failed_custom_edit_never_sends_an_unowned_picker(self):
        worker, box, live, entry, card = self.start_question()
        data = card["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
        original_api = self.telegram._api_call.side_effect

        def failed_edit(api, method, **payload):
            result = original_api(api, method, **payload)
            return None if method == "editMessageText" else result

        before = len(self.calls)
        with mock.patch.object(self.telegram, "_api_call", side_effect=failed_edit):
            self.telegram._handle_ask_callback("fixture", 1, entry.message_id, data)
        calls = self.calls[before:]
        self.assertEqual([method for method, payload in calls], ["editMessageText"])
        self.assertEqual(calls[0][1]["message_id"], entry.message_id)
        self.assertFalse(entry.event.is_set())
        self.assertTrue(self.interaction.answer("telegram:1", "Still accepts text"))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(box["result"], "Still accepts text")

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
