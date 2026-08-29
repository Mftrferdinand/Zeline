"""Contract tests for drain-aware gateway restart and update.

A restart that SIGKILLs an in-flight agent turn silently destroys work the
user asked for. These tests pin the drain path end to end:

- SessionStore can pause (refuse new turns), report who is busy, and wait.
- The gateway process exits only after active turns finish (SIGUSR1 path).
- `zeline update` drains a live gateway before mutating the venv and brings
  it back afterwards.
- Windows, which has no SIGUSR1, falls back to the existing stop path.
"""
from __future__ import annotations

import importlib
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_modules(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "zeline" or module_name.startswith("zeline."):
            sys.modules.pop(module_name, None)
    cfg = importlib.import_module("zeline.config")
    service = importlib.import_module("zeline.gateway_service")
    sessions = importlib.import_module("zeline.sessions")
    return cfg, service, sessions


class DrainConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_home = os.environ.get("ZELINE_HOME")

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved_home
        self._tmp.cleanup()

    def test_fresh_install_has_a_positive_drain_timeout(self):
        cfg, _service, _sessions = fresh_modules(self.home)
        self.assertGreater(cfg.RESTART_DRAIN_TIMEOUT, 0)
        defaults = cfg.config_copy()
        self.assertIn("restart_drain_timeout", defaults["agent"])

    def test_drain_timeout_is_configurable_and_never_negative(self):
        cfg, _service, _sessions = fresh_modules(self.home)
        saved = cfg.config_copy()
        saved["agent"]["restart_drain_timeout"] = -5
        cfg.save_config(saved)
        self.assertEqual(cfg.RESTART_DRAIN_TIMEOUT, 0.0)

        saved["agent"]["restart_drain_timeout"] = 90
        cfg.save_config(saved)
        self.assertEqual(cfg.RESTART_DRAIN_TIMEOUT, 90.0)

    def test_garbage_drain_timeout_falls_back_to_default(self):
        cfg, _service, _sessions = fresh_modules(self.home)
        saved = cfg.config_copy()
        saved["agent"]["restart_drain_timeout"] = "not-a-number"
        cfg.save_config(saved)
        self.assertEqual(cfg.RESTART_DRAIN_TIMEOUT, float(cfg.DEFAULT_RESTART_DRAIN_TIMEOUT))


class SessionDrainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_home = os.environ.get("ZELINE_HOME")
        _cfg, _service, self.sessions_mod = fresh_modules(self.home)

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved_home
        self._tmp.cleanup()

    def _store(self):
        return self.sessions_mod.SessionStore(persistence=None)

    def test_pause_refuses_new_turns_without_touching_the_agent(self):
        store = self._store()
        store.pause()
        self.assertTrue(store.paused)
        with mock.patch.object(store, "get_or_create") as get_or_create:
            reply = store.send("telegram:1", "hello", "safe")
        # A paused store must not even construct/lookup a session.
        get_or_create.assert_not_called()
        self.assertIn("restarting", reply.casefold())

    def test_resume_accepts_turns_again(self):
        store = self._store()
        store.pause()
        store.resume()
        self.assertFalse(store.paused)

    def test_drain_returns_immediately_when_idle(self):
        store = self._store()
        started = time.monotonic()
        finished, busy = store.drain(timeout=5.0)
        self.assertTrue(finished)
        self.assertEqual(busy, [])
        self.assertLess(time.monotonic() - started, 2.0)
        # drain() must leave the store paused so no new work slips in.
        self.assertTrue(store.paused)

    def test_drain_waits_for_a_running_turn_then_reports_success(self):
        store = self._store()
        session = mock.Mock()
        session.running = True
        store._sessions["telegram:1"] = session
        self.assertEqual(store.running_identities(), ["telegram:1"])

        def finish_soon():
            time.sleep(0.6)
            session.running = False

        worker = threading.Thread(target=finish_soon, daemon=True)
        worker.start()
        started = time.monotonic()
        finished, busy = store.drain(timeout=10.0, poll=0.05)
        elapsed = time.monotonic() - started
        worker.join(timeout=2)
        self.assertTrue(finished)
        self.assertEqual(busy, [])
        # It really waited rather than returning instantly.
        self.assertGreater(elapsed, 0.4)

    def test_drain_reports_who_is_still_busy_on_timeout(self):
        store = self._store()
        stuck = mock.Mock()
        stuck.running = True
        idle = mock.Mock()
        idle.running = False
        store._sessions["telegram:stuck"] = stuck
        store._sessions["telegram:idle"] = idle
        finished, busy = store.drain(timeout=0.3, poll=0.05)
        self.assertFalse(finished)
        self.assertEqual(busy, ["telegram:stuck"])


class GatewayDrainStopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_home = os.environ.get("ZELINE_HOME")
        self.cfg, self.service, _sessions = fresh_modules(self.home)

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved_home
        self._tmp.cleanup()

    @unittest.skipIf(not hasattr(signal, "SIGUSR1"), "POSIX-only signal path")
    def test_sigusr1_is_sent_before_any_force_kill(self):
        state = {"pid": 4321, "start_token": "t", "start_ticks": "1", "drain": True}
        sent: list[int] = []
        matches = [True, True, False]

        def fake_signal(pid, sig):
            sent.append(sig)
            return True

        with mock.patch.object(self.service, "_load_state", return_value=state), \
             mock.patch.object(self.service, "_process_matches_state", side_effect=lambda _s: matches.pop(0) if matches else False), \
             mock.patch.object(self.service, "_signal_process", side_effect=fake_signal), \
             mock.patch.object(self.service, "_remove_state") as remove, \
             mock.patch.object(self.service, "stop") as hard_stop:
            ok, message = self.service.drain_then_stop(drain_timeout=5.0)

        self.assertTrue(ok)
        # Only the graceful signal was used; the force path never ran.
        self.assertEqual(sent, [signal.SIGUSR1])
        hard_stop.assert_not_called()
        remove.assert_called_once()
        self.assertIn("drained", message.casefold())

    @unittest.skipIf(not hasattr(signal, "SIGUSR1"), "POSIX-only signal path")
    def test_failed_drain_escalates_and_says_work_may_be_cut_off(self):
        state = {"pid": 4321, "start_token": "t", "start_ticks": "1", "drain": True}
        with mock.patch.object(self.service, "_load_state", return_value=state), \
             mock.patch.object(self.service, "_process_matches_state", return_value=True), \
             mock.patch.object(self.service, "_signal_process", return_value=True), \
             mock.patch.object(self.service, "stop", return_value=(True, "Gateway stopped (managed, PID 4321).")) as hard_stop:
            ok, message = self.service.drain_then_stop(drain_timeout=0.0 + 0.2)
        self.assertTrue(ok)
        hard_stop.assert_called_once()
        self.assertIn("cut off", message.casefold())

    def test_zero_timeout_keeps_the_old_immediate_stop(self):
        with mock.patch.object(self.service, "stop", return_value=(True, "Gateway stopped.")) as hard_stop, \
             mock.patch.object(self.service, "_signal_process") as sig:
            ok, _message = self.service.drain_then_stop(drain_timeout=0)
        self.assertTrue(ok)
        hard_stop.assert_called_once()
        sig.assert_not_called()

    def test_windows_falls_back_to_stop_because_it_has_no_sigusr1(self):
        with mock.patch.object(self.service, "IS_WINDOWS", True), \
             mock.patch.object(self.service, "stop", return_value=(True, "Gateway stopped.")) as hard_stop, \
             mock.patch.object(self.service, "_signal_process") as sig:
            ok, _message = self.service.drain_then_stop(drain_timeout=30)
        self.assertTrue(ok)
        hard_stop.assert_called_once()
        sig.assert_not_called()

    def test_no_running_gateway_delegates_to_stop(self):
        with mock.patch.object(self.service, "_load_state", return_value=None), \
             mock.patch.object(self.service, "stop", return_value=(False, "Gateway is not running.")) as hard_stop:
            ok, message = self.service.drain_then_stop(drain_timeout=30)
        self.assertFalse(ok)
        hard_stop.assert_called_once()
        self.assertIn("not running", message.casefold())

    @unittest.skipIf(not hasattr(signal, "SIGUSR1"), "POSIX-only signal path")
    def test_gateway_without_the_drain_marker_is_never_signalled(self):
        """A pre-drain child has no SIGUSR1 handler; the default action kills it.

        Signalling it would be WORSE than the old behaviour — SIGUSR1's default
        POSIX disposition terminates the process immediately, skipping the
        graceful SIGTERM phase. So an unmarked state must fall back to `stop`
        and say why.
        """
        legacy_state = {"pid": 4321, "start_token": "t", "start_ticks": "1"}
        with mock.patch.object(self.service, "_load_state", return_value=legacy_state), \
             mock.patch.object(self.service, "_process_matches_state", return_value=True), \
             mock.patch.object(self.service, "_signal_process") as sig, \
             mock.patch.object(self.service, "stop", return_value=(True, "Gateway stopped (managed, PID 4321).")) as hard_stop:
            ok, message = self.service.drain_then_stop(drain_timeout=30)
        self.assertTrue(ok)
        sig.assert_not_called()
        hard_stop.assert_called_once()
        self.assertIn("predates drain support", message.casefold())

    def test_start_marks_new_children_as_drain_capable(self):
        """`drain_then_stop` relies on this flag; pin that `start` writes it."""
        source = Path(str(self.service.__file__)).read_text(encoding="utf-8")
        self.assertIn('"drain": True', source)


class UpdaterGatewayLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_home = os.environ.get("ZELINE_HOME")
        fresh_modules(self.home)
        self.updater = importlib.import_module("zeline.updater")
        self.service = importlib.import_module("zeline.gateway_service")

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved_home
        self._tmp.cleanup()

    def test_update_drains_a_live_gateway_before_touching_the_venv(self):
        order: list[str] = []
        fake_root = Path("/tmp/zeline-checkout")

        with mock.patch.object(self.service, "status", return_value=(True, "running", {"pid": 9})), \
             mock.patch.object(self.service, "drain_then_stop", side_effect=lambda: (order.append("drain"), (True, "drained"))[1]), \
             mock.patch.object(self.service, "start", side_effect=lambda *a, **k: (order.append("start"), (True, "started"))[1]), \
             mock.patch.object(self.updater, "_checkout_root", return_value=fake_root), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.updater, "_run_installer", side_effect=lambda *a, **k: (order.append("install"), 0)[1]):
            code = self.updater.update()

        self.assertEqual(code, 0)
        # The venv must never be mutated while the gateway still runs.
        self.assertEqual(order, ["drain", "install", "start"])

    def test_update_leaves_a_stopped_gateway_alone(self):
        fake_root = Path("/tmp/zeline-checkout")
        with mock.patch.object(self.service, "status", return_value=(False, "not running", None)), \
             mock.patch.object(self.service, "drain_then_stop") as drain, \
             mock.patch.object(self.service, "start") as start, \
             mock.patch.object(self.updater, "_checkout_root", return_value=fake_root), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.updater, "_run_installer", return_value=0):
            code = self.updater.update()
        self.assertEqual(code, 0)
        drain.assert_not_called()
        start.assert_not_called()

    def test_a_paused_gateway_is_restarted_even_when_the_update_fails(self):
        with mock.patch.object(self.service, "status", return_value=(True, "running", {"pid": 9})), \
             mock.patch.object(self.service, "drain_then_stop", return_value=(True, "drained")), \
             mock.patch.object(self.service, "start", return_value=(True, "started")) as start, \
             mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", side_effect=RuntimeError("offline")):
            code = self.updater.update()
        self.assertEqual(code, 1)
        # Never leave the user's gateway down because an update failed.
        start.assert_called_once()

    def test_checksum_mismatch_still_restores_the_gateway(self):
        installer_name = self.updater._installer_name()

        def fake_get(url, accept=""):
            if url.endswith("SHA256SUMS"):
                return b"a" * 64 + f"  {installer_name}\n".encode()
            if url.endswith(installer_name):
                return b"tampered"
            raise AssertionError(url)

        with mock.patch.object(self.service, "status", return_value=(True, "running", {"pid": 9})), \
             mock.patch.object(self.service, "drain_then_stop", return_value=(True, "drained")), \
             mock.patch.object(self.service, "start", return_value=(True, "started")) as start, \
             mock.patch.object(self.updater, "_checkout_root", return_value=None), \
             mock.patch.object(self.updater, "_latest_tag", return_value="v9.9.9"), \
             mock.patch.object(self.updater, "_https_get", side_effect=fake_get), \
             mock.patch.object(self.updater, "_run_installer") as run:
            code = self.updater.update()
        self.assertEqual(code, 1)
        run.assert_not_called()
        start.assert_called_once()

    def test_pause_helper_survives_a_broken_gateway_service(self):
        with mock.patch.object(self.service, "status", side_effect=RuntimeError("boom")):
            self.assertFalse(self.updater._pause_gateway_for_update())


if __name__ == "__main__":
    unittest.main()
