"""Offline event-synchronized control-command regressions (no real transport)."""
import importlib
import threading
from types import SimpleNamespace
from unittest import mock

from tests.test_ask_user import AskUserBase


class TelegramCommandLatencyTests(AskUserBase):
    def setUp(self):
        super().setUp()
        self.tg = importlib.import_module("zeline.gateways.telegram")
        self.stop = threading.Event()
        self.sessions = SimpleNamespace()

    def dispatch(self, text):
        self.tg._dispatch_update("fixture", "fixture", self.sessions,
            {"message": {"chat": {"id": 1}, "message_id": 7, "text": text}},
            allowed=[1], tool_profile="default", stop_event=self.stop)

    def test_start_transport_does_not_block_polling(self):
        entered, release, returned = (threading.Event() for _ in range(3))
        def api(*args, **kwargs):
            entered.set()
            release.wait(3)
        with mock.patch.object(self.tg, "_api_call", side_effect=api):
            worker = threading.Thread(target=lambda: (self.dispatch("/start"), returned.set()))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(returned.wait(.5), "polling blocked by command transport")
            finally:
                release.set()
                worker.join(2)

    def test_model_provider_menu_does_not_refresh_all_catalogs(self):
        providers = [{"slug": "fixture", "name": "Fixture", "base_url": "https://fixture.invalid", "model": "m"}]
        with mock.patch.object(self.tg, "_configured_providers", return_value=providers), \
             mock.patch.object(self.tg, "_active_provider_slug", return_value="fixture"), \
             mock.patch.object(self.tg, "_fetch_models_catalog") as fetch, \
             mock.patch.object(self.tg, "_api_call") as api:
            self.tg._handle_command_update("fixture", "/model", self.sessions, "telegram:1", 1,
                stop_event=self.stop, tool_profile="default")
            fetch.assert_not_called()
            self.assertIn("reply_markup", api.call_args.kwargs)

    def test_callback_ack_does_not_gate_picker_edit(self):
        ack, release, edited, done = (threading.Event() for _ in range(4))
        def api(*args, **kwargs):
            ack.set()
            release.wait(3)
        with mock.patch.object(self.tg, "_api_call", side_effect=api), \
             mock.patch.object(self.tg, "_edit_interactive", side_effect=lambda *a, **k: edited.set()):
            worker = threading.Thread(target=lambda: (self.tg._handle_callback("fixture",
                {"id": "fixture", "data": "model:cancel", "message": {"chat": {"id": 1}}},
                self.sessions), done.set()))
            worker.start()
            try:
                self.assertTrue(ack.wait(1))
                self.assertTrue(edited.wait(.5), "picker waits for callback acknowledgement HTTP")
            finally:
                release.set()
                worker.join(2)
                self.assertTrue(done.wait(1))

    def test_new_blocks_duplicate_reset_and_turn_admission_but_not_start(self):
        entered, release, reset_done, start_sent = (threading.Event() for _ in range(4))
        def reset(identity):
            entered.set()
            release.wait(3)
            reset_done.set()
        self.sessions = SimpleNamespace(stop=mock.Mock(), request_stop=mock.Mock(),
            reflect=mock.Mock(return_value=None), reset=mock.Mock(side_effect=reset))
        def api(api, method, **kwargs):
            if kwargs.get("text") == self.tg._command_setup_text():
                start_sent.set()
            return {"ok": True}
        with mock.patch.object(self.tg, "_api_call", side_effect=api), \
             mock.patch.object(self.tg, "_new_session_text", return_value="fresh"), \
             mock.patch.object(self.tg, "_start_agent_reply") as turn:
            returned = threading.Event()
            worker = threading.Thread(target=lambda: (self.dispatch("/new"), returned.set()))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(returned.wait(.5), "reset blocks polling")
                self.dispatch("/new")
                self.dispatch("next task")
                self.dispatch("/start")
                self.assertTrue(start_sent.wait(1))
                self.assertEqual(self.sessions.reset.call_count, 1)
                turn.assert_not_called()
            finally:
                release.set()
                worker.join(2)
                self.assertTrue(reset_done.wait(1))
                # Wait for reset command ownership to be released before teardown.
                for thread in threading.enumerate():
                    if thread.name == "zeline-command-1":
                        thread.join(2)
            self.dispatch("next task")
            turn.assert_called_once()
            self.sessions.request_stop.assert_called_once_with("telegram:1")
            self.sessions.stop.assert_not_called()

    def test_reset_failure_releases_gate_without_success_card(self):
        self.sessions = SimpleNamespace(request_stop=mock.Mock(), reflect=mock.Mock(return_value=None),
                                        reset=mock.Mock(side_effect=RuntimeError("fixture")))
        with mock.patch.object(self.tg, "_api_call", return_value={"ok": True}) as api, \
             mock.patch.object(self.tg, "_new_session_text") as card, \
             mock.patch.object(self.tg, "_start_agent_reply") as turn:
            self.dispatch("/new")
            for thread in threading.enumerate():
                if thread.name == "zeline-command-1":
                    thread.join(2)
            card.assert_not_called()
            self.assertTrue(any("No completion confirmed" in c.kwargs.get("text", "") for c in api.call_args_list))
            self.dispatch("next task")
            turn.assert_called_once()

    def test_review_failure_is_best_effort_reset_still_finishes(self):
        self.sessions = SimpleNamespace(request_stop=mock.Mock(), reflect=mock.Mock(side_effect=RuntimeError("fixture")),
                                        reset=mock.Mock())
        with mock.patch.object(self.tg, "_api_call", return_value={"ok": True}), \
             mock.patch.object(self.tg, "_new_session_text", return_value="fresh") as card:
            self.dispatch("/new")
            for thread in threading.enumerate():
                if thread.name == "zeline-command-1":
                    thread.join(2)
            self.sessions.reset.assert_called_once()
            card.assert_called_once()

    def test_catalog_fallbacks_share_one_deadline(self):
        clock = [100.0]
        budgets = []
        def get(url, **kwargs):
            budgets.append(kwargs["timeout"])
            clock[0] += 7
            return SimpleNamespace(ok=False)
        # Execute IO synchronously under a deterministic fake clock: no sleeps.
        with mock.patch.object(self.tg.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(self.tg._HTTP, "get", side_effect=get), \
             mock.patch("zeline.cancellation.wait_io", side_effect=lambda call, *a, **k: call()):
            self.tg._fetch_models_catalog("https://deadline.fixture.invalid", "", timeout=12, force_refresh=True)
        self.assertEqual(budgets, [12, 5])

    def test_provider_selection_refreshes_only_selected_catalog(self):
        provider = {"slug": "fixture", "name": "Fixture", "model": "m"}
        with mock.patch.object(self.tg, "_configured_providers", return_value=[provider]), \
             mock.patch.object(self.tg, "_discover_provider_models", return_value=["m"]) as discover, \
             mock.patch.object(self.tg, "_edit_interactive"):
            self.tg._handle_callback("fixture", {"data": "provider:0", "message": {"chat": {"id": 1}}}, self.sessions)
            discover.assert_called_once_with(provider, force_refresh=True)
