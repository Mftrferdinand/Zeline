"""A lost Bot API response must not create another running-status bubble."""
import importlib
from unittest import mock

from tests.test_ask_user import AskUserBase


class ProgressDeliveryTests(AskUserBase):
    def setUp(self):
        super().setUp()
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_unknown_send_result_is_not_retried_by_each_heartbeat(self):
        with mock.patch.object(self.telegram, "_api_call", return_value=None) as api:
            live = self.telegram._LiveStatus("fixture", 1)
            live.turn_started -= 160
            live.add("🌐 Opening webpage: example.org")
            for _ in range(3):
                live.turn_started -= 6
                live.tick()
            live.detach()
            live.add("📖 Reading webpage content")
            live.finalize()
        sends = [c for c in api.call_args_list if c.args[1] == "sendMessage"]
        self.assertEqual(len(sends), 1, "unknown delivery was retried and may duplicate messages")
        self.assertNotIn("Working", sends[0].kwargs["text"], "untracked message cannot be cleaned later")
        self.assertNotIn("/stop", sends[0].kwargs["text"])

    def test_failed_detach_does_not_abandon_the_old_working_message(self):
        calls = []

        def api(_api, method, **payload):
            calls.append((method, payload))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 7}}
            return None

        with mock.patch.object(self.telegram, "_api_call", side_effect=api):
            live = self.telegram._LiveStatus("fixture", 1)
            live.turn_started -= 160
            live.add("🌐 Opening webpage: example.org")
            live.detach()  # Failed cleanup: it may still contain Working.
            live.add("📖 Reading webpage content")
            live.turn_started -= 6
            live.tick()
        self.assertEqual(sum(m == "sendMessage" for m, _ in calls), 1)
        self.assertEqual(live.message_id, 7)

    def test_failed_waiting_edit_is_retried_without_creating_a_new_message(self):
        calls = []
        failures_left = 1

        def api(_api, method, **payload):
            nonlocal failures_left
            calls.append((method, payload))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 7}}
            if "Waiting for your answer" in payload.get("text", "") and failures_left:
                failures_left -= 1
                return None
            return {"ok": True}

        with mock.patch.object(self.telegram, "_api_call", side_effect=api):
            live = self.telegram._LiveStatus("fixture", 1)
            live.add("Reading webpage content")
            live.set_awaiting_user()
            live.tick()  # The text is unchanged, but the first edit failed.
        waiting = [p for m, p in calls if m == "editMessageText"
                   and "Waiting for your answer" in p.get("text", "")]
        self.assertEqual(len(waiting), 2)
        self.assertEqual(sum(m == "sendMessage" for m, _ in calls), 1)

    def test_failed_detach_then_recovery_cleans_the_original_id(self):
        edits = []
        online = False

        def api(_api, method, **payload):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 7}}
            edits.append(payload)
            return {"ok": True} if online else None

        with mock.patch.object(self.telegram, "_api_call", side_effect=api):
            live = self.telegram._LiveStatus("fixture", 1)
            live.turn_started -= 160
            live.add("Reading webpage content")
            live.detach()
            online = True
            live.finalize()
        self.assertEqual(edits[-1]["message_id"], 7)
        self.assertNotIn("Working", edits[-1]["text"])
        self.assertNotIn("/stop", edits[-1]["text"])
        self.assertIn("Reading webpage content", edits[-1]["text"])

    def test_successful_delivery_uses_one_id_and_final_removes_status(self):
        calls = []

        def api(_api, method, **payload):
            calls.append((method, payload))
            return {"ok": True, "result": {"message_id": 7}} if method == "sendMessage" else {"ok": True}

        with mock.patch.object(self.telegram, "_api_call", side_effect=api):
            live = self.telegram._LiveStatus("fixture", 1)
            live.turn_started -= 160
            live.add("🌐 Opening webpage: example.org")
            for _ in range(3):
                live.turn_started -= 6
                live.tick()
            live.finalize()
            before = len(calls)
            live.tick()
            live.finalize()
            self.assertEqual(len(calls), before)
        self.assertEqual(sum(m == "sendMessage" for m, _ in calls), 1)
        edits = [p for m, p in calls if m == "editMessageText"]
        self.assertTrue(any("Working" in p["text"] for p in edits))
        self.assertEqual({p["message_id"] for p in edits}, {7})
        self.assertNotIn("Working", edits[-1]["text"])
        self.assertNotIn("/stop", edits[-1]["text"])
        self.assertIn("Opening webpage", edits[-1]["text"])
