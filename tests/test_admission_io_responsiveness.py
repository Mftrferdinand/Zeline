"""Slow history restore cannot hold cancellation for unrelated active work."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


class AdmissionIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zeline-admission-io-")
        self.addCleanup(self.temp.cleanup)
        env = mock.patch.dict(os.environ, {
            "ZELINE_HOME": str(Path(self.temp.name) / "state"),
            "ZELINE_API_KEY": "offline-fixture", "ZELINE_MODEL": "offline-model",
            "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        })
        env.start()
        self.addCleanup(env.stop)
        for name in tuple(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.sessions = importlib.import_module("zeline.sessions")
        self.sessions.config.PERSIST_SESSIONS = False

    def test_cold_history_restore_does_not_block_stop_for_active_identity(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

        class Persistence:
            def load(self, identity):
                if identity == "cold":
                    entered.set()
                    release.wait(1.5)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = self.sessions.SessionStore(persistence=Persistence())
        active = store.admit("active", "safe")
        self.assertTrue(store.wait_turn(active))
        cold = threading.Thread(target=lambda: store.admit("cold", "safe"), daemon=True)
        cold.start()
        self.assertTrue(entered.wait(1))
        def stop():
            store.request_stop("active")
            stopped.set()
        worker = threading.Thread(target=stop, daemon=True)
        try:
            with mock.patch.object(self.sessions.tools, "cancel_identity", return_value=0):
                worker.start()
                self.assertTrue(stopped.wait(.15), "Cold DB read held the manager lock over /stop")
                self.assertTrue(active.cancel_event.is_set())
        finally:
            release.set()
            cold.join(2)
            worker.join(2)
            store.finish_turn(active)

    def test_same_identity_cold_restore_is_single_flight(self):
        entered, release = threading.Event(), threading.Event()
        calls, sessions, errors = [], [], []

        class Persistence:
            def load(self, identity):
                calls.append(identity)
                entered.set()
                release.wait(1)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = self.sessions.SessionStore(persistence=Persistence())
        def get():
            try:
                sessions.append(store.get_or_create("same", "safe"))
            except Exception as exc:
                errors.append(exc)
        first = threading.Thread(target=get, daemon=True)
        second = threading.Thread(target=get, daemon=True)
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        release.set()
        first.join(2)
        second.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(calls, ["same"])
        self.assertEqual(len(sessions), 2)
        self.assertIs(sessions[0], sessions[1])

    def test_reset_during_cold_restore_cannot_resurrect_old_history(self):
        entered, release = threading.Event(), threading.Event()
        result, errors = [], []

        class Persistence:
            def load(self, identity):
                entered.set()
                release.wait(1)
                return [], "Old title"
            def recovery_present(self, identity):
                return False
            def reset(self, identity):
                return True

        store = self.sessions.SessionStore(persistence=Persistence())
        def get():
            try:
                result.append(store.admit("resetting", "safe"))
            except self.sessions.ZelineError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=get, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            store.reset("resetting")
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("resetting", store._sessions, "Old restore committed after reset")
        self.assertTrue(errors or result == [None])

    def test_stop_during_cold_restore_invalidates_unstarted_work(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []

        class Persistence:
            def load(self, identity):
                entered.set()
                release.wait(1)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = self.sessions.SessionStore(persistence=Persistence())
        def admit():
            try:
                outcomes.append(store.admit("loading", "safe"))
            except self.sessions.ZelineError:
                outcomes.append("cancelled")
        worker = threading.Thread(target=admit, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            with mock.patch.object(self.sessions.tools, "cancel_identity", return_value=0):
                self.assertTrue(store.request_stop("loading"), "Loading is active cancellable work")
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(outcomes, ["cancelled"])
        self.assertNotIn("loading", store._sessions)

    def test_pending_cold_admissions_preserve_order_and_stop_discards_both(self):
        entered, release = threading.Event(), threading.Event()

        class Persistence:
            def load(self, identity):
                entered.set()
                release.wait(1)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = self.sessions.SessionStore(persistence=Persistence())
        first = store.begin_admission("cold-order", "safe")
        self.assertTrue(entered.wait(1))
        second = store.begin_admission("cold-order", "safe")
        try:
            with mock.patch.object(self.sessions.tools, "cancel_identity", return_value=0):
                self.assertTrue(store.request_stop("cold-order"))
            self.assertTrue(first.cancel_event.is_set())
            self.assertTrue(second.cancel_event.is_set())
        finally:
            release.set()
            self.assertTrue(first.ready.wait(2))
            self.assertTrue(second.ready.wait(2))
        self.assertIsNone(first.ticket)
        self.assertIsNone(second.ticket)
        self.assertNotIn("cold-order", store._sessions)

    def test_drain_reports_a_pending_restore_as_busy(self):
        entered, release = threading.Event(), threading.Event()

        class Persistence:
            def load(self, identity):
                entered.set()
                release.wait(1)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = self.sessions.SessionStore(persistence=Persistence())
        pending = store.begin_admission("cold-drain", "safe")
        self.assertTrue(entered.wait(1))
        try:
            drained, identities = store.drain(timeout=0)
            self.assertFalse(drained, "Restart must not claim a pending restore is idle")
            self.assertIn("cold-drain", identities)
        finally:
            release.set()
            self.assertTrue(pending.ready.wait(2))

    def test_stop_releases_cold_reservation_without_waiting_for_the_old_db_read(self):
        entered, release = threading.Event(), threading.Event()

        class Persistence:
            def load(self, identity):
                entered.set()
                release.wait(1)
                return [], ""
            def recovery_present(self, identity):
                return False

        store = self.sessions.SessionStore(persistence=Persistence())
        pending = store.begin_admission("cold-unblock", "safe")
        self.assertTrue(entered.wait(1))
        try:
            with mock.patch.object(self.sessions.tools, "cancel_identity", return_value=0):
                store.request_stop("cold-unblock")
            self.assertTrue(pending.ready.wait(.15), "Cancelled DB read kept the identity's admission lock")
            self.assertIsNone(pending.ticket)
        finally:
            release.set()
            pending.ready.wait(2)


if __name__ == "__main__":
    unittest.main()
