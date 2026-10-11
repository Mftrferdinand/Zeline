"""Tests for zeline/memory_autosync.py — all syncers are mocked, no real Gmail."""

import argparse
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _fresh_module(tmpdir):
    """Import a fresh copy of memory_autosync with isolated DATA_DIR."""
    import importlib
    import zeline.memory_autosync as ma

    importlib.reload(ma)
    ma._SETTINGS_OVERRIDE.clear()
    fake_data = Path(tmpdir) / ".zeline"
    fake_data.mkdir(parents=True, exist_ok=True)
    patcher = patch("zeline.config.DATA_DIR", fake_data)
    patcher.start()
    return ma, patcher


class AutosyncSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ma, self.patcher = _fresh_module(self.tmp.name)
        self.addCleanup(self.patcher.stop)

    def test_all_sources_off_by_default(self):
        """(a) Privacy default: nothing is enabled out of the box."""
        self.assertFalse(self.ma.is_enabled("gmail"))
        self.assertFalse(self.ma.is_enabled("github"))
        self.assertFalse(self.ma.is_enabled("calendar"))

    def test_kill_switch(self):
        with patch.dict("os.environ", {"ZELINE_AUTOSYNC_OFF": "1"}):
            self.ma.configure(sources=["gmail"], persist=False)
            self.assertFalse(self.ma.is_enabled("gmail"))
        self.assertTrue(self.ma.is_enabled("gmail"))

    def test_configure_interval(self):
        """(e) configure() sets the interval."""
        self.ma.configure(interval_min=45, persist=False)
        self.assertEqual(self.ma._settings()["interval_min"], 45)

    def test_should_sync_respects_interval(self):
        """(e) due only when the interval has elapsed."""
        self.ma.configure(sources=["gmail"], interval_min=20, persist=False)
        state = {"gmail": {"last_sync": time.time()}}
        self.ma._save_state(state)
        self.assertFalse(self.ma.should_sync("gmail"))
        state["gmail"]["last_sync"] = time.time() - 21 * 60
        self.ma._save_state(state)
        self.assertTrue(self.ma.should_sync("gmail"))
        self.assertIn("gmail", self.ma.due_sources())

    def test_unknown_source(self):
        with self.assertRaises(self.ma.AutosyncError):
            self.ma.sync_source("nope")


class AutosyncSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ma, self.patcher = _fresh_module(self.tmp.name)
        self.addCleanup(self.patcher.stop)
        # Mock memory writes — never touch the real memory store.
        self.mem = MagicMock()
        self.mem.list_episodes.return_value = []
        self.mem.add_episode.return_value = "episode-id-1"
        self.ma.mem = self.mem
        # Mock syncers with fixed summaries.
        self.ma.SYNCERS = {
            "gmail": MagicMock(return_value="Gmail summary"),
            "github": MagicMock(return_value="GitHub summary"),
            "calendar": MagicMock(return_value="Calendar summary"),
        }

    def _enable_gmail(self):
        self.ma.configure(sources=["gmail"], persist=False)

    def test_sync_enabled_source_writes_summary_and_state(self):
        """(b) Enabling a source + sync writes the summary and updates state."""
        self._enable_gmail()
        result = self.ma.sync_source("gmail")
        self.assertIn("Gmail summary", result)
        self.ma.SYNCERS["gmail"].assert_called_once()
        self.mem.add_episode.assert_called_once()
        args = self.mem.add_episode.call_args[0]
        self.assertIn("Gmail", args[1])  # title mentions source
        self.assertEqual(args[2], ["Gmail summary"])
        state = self.ma._load_state()
        self.assertIn("last_sync", state["gmail"])
        self.assertTrue(abs(state["gmail"]["last_sync"] - time.time()) < 60)

    def test_no_duplicate_same_day(self):
        """(c) A second sync on the same day does not write a duplicate."""
        self._enable_gmail()
        title = self.ma._episode_title("gmail", self.ma._today_str())
        first = self.ma.sync_source("gmail")
        self.assertIn("recorded", first)
        # Simulate the episode now existing in memory.
        self.mem.list_episodes.return_value = [{"title": title}]
        second = self.ma.sync_source("gmail")
        self.assertIn("skipped", second)
        # The memory write happened exactly once.
        self.assertEqual(self.mem.add_episode.call_count, 1)

    def test_failing_source_does_not_break_sync_all(self):
        """(d) One failing source never fails the others."""
        self.ma.configure(sources=["gmail", "github", "calendar"], persist=False)
        self.ma.SYNCERS["github"].side_effect = self.ma.AutosyncError("boom")
        results = self.ma.sync_all()
        self.assertTrue(results["github"].startswith("ERROR"))
        self.assertIn("Gmail summary", results["gmail"])
        self.assertIn("Calendar summary", results["calendar"])

    def test_disabled_source_skipped(self):
        result = self.ma.sync_source("gmail")
        self.assertIn("not enabled", result)
        self.ma.SYNCERS["gmail"].assert_not_called()

    def test_state_file_mode(self):
        """Audit: state file must be 0600."""
        self._enable_gmail()
        self.ma.sync_source("gmail")
        mode = self.ma._state_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class AutosyncGmailParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ma, self.patcher = _fresh_module(self.tmp.name)
        self.addCleanup(self.patcher.stop)

    def _gmail_lines(self):
        return "\n".join(
            [
                "id1 | 11 Oct 2026 | alice@example.com | URGENT: invoice due",
                "id2 | 11 Oct 2026 | bob@example.com | lunch plans",
            ]
        )

    def test_sync_gmail_summary_uses_metadata_only(self):
        """The gmail syncer never calls gmail_read (no bodies)."""
        conn = MagicMock()
        conn.status.return_value = {"connected": True}
        conn.gmail_search.return_value = self._gmail_lines()
        with patch.object(self.ma, "_get_connector", return_value=conn):
            summary = self.ma.sync_gmail({})
        conn.gmail_read.assert_not_called()
        self.assertIn("2 email", summary)
        self.assertIn("1 penting", summary)
        self.assertIn("URGENT: invoice due", summary)

    def test_sync_gmail_not_connected(self):
        conn = MagicMock()
        conn.status.return_value = {"connected": False}
        with (
            patch("zeline.connectors.get", return_value=conn),
            self.assertRaises(self.ma.AutosyncError),
        ):
            self.ma.sync_gmail({})

    def test_sync_github_requires_repos(self):
        conn = MagicMock()
        conn.status.return_value = {"connected": True}
        with (
            patch("zeline.connectors.get", return_value=conn),
            self.assertRaises(self.ma.AutosyncError),
        ):
            self.ma.sync_github({})


class AutosyncCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ma, self.patcher = _fresh_module(self.tmp.name)
        self.addCleanup(self.patcher.stop)

    def _parser(self):
        # Faithful replica of zeline/cli.py's `memory` parser (pre-integration).
        parser = argparse.ArgumentParser(prog="zeline")
        mem_p = parser.add_subparsers(dest="command").add_parser("memory")
        mem_p.add_argument("action", nargs="?", choices=["consolidate"])
        self.ma.register_memory_sub(mem_p)
        return parser

    def test_register_exposes_subcommands(self):
        """(f) register_memory_sub exposes sync / autosync on|off|status."""
        parser = self._parser()
        ns = parser.parse_args(["memory", "sync"])
        self.assertEqual(ns.memory_sub, "sync")
        self.assertIsNone(ns.source)
        ns = parser.parse_args(["memory", "sync", "gmail"])
        self.assertEqual(ns.source, "gmail")
        ns = parser.parse_args(["memory", "autosync", "status"])
        self.assertEqual(ns.autosync_cmd, "status")
        for cmd in ("on", "off", "status"):
            ns = parser.parse_args(["memory", "autosync", cmd])
            self.assertTrue(callable(ns.func))
        # Legacy `zeline memory consolidate` still works (now a subcommand).
        ns = parser.parse_args(["memory", "consolidate"])
        self.assertEqual(ns.memory_sub, "consolidate")
        self.assertTrue(callable(ns.func))

    def test_legacy_positional_removed(self):
        """The old `action` positional is migrated away (it broke subparsers)."""
        parser = self._parser()
        mem_parser_actions = None
        for act in parser._actions:
            if isinstance(act, argparse._SubParsersAction):
                for name, sub in act.choices.items():
                    if name == "memory":
                        mem_parser_actions = sub._actions
        dests = [a.dest for a in mem_parser_actions]
        self.assertNotIn("action", dests)

    def test_dispatch_memory_runs_subcommand(self):
        parser = self._parser()
        ns = parser.parse_args(["memory", "autosync", "status"])
        with patch.object(
            self.ma,
            "status",
            return_value={
                "kill_switch": False,
                "master_enabled": True,
                "interval_min": 20,
                "sources": {},
            },
        ):
            self.assertEqual(self.ma.dispatch_memory(ns), 0)

    def test_dispatch_memory_ignores_plain_action(self):
        parser = self._parser()
        ns = parser.parse_args(["memory"])
        self.assertIsNone(self.ma.dispatch_memory(ns))

    def test_consolidate_delegates_to_cmd_memory(self):
        parser = self._parser()
        ns = parser.parse_args(["memory", "consolidate"])
        with patch("zeline.cli.cmd_memory", return_value=0) as cm:
            self.assertEqual(self.ma.dispatch_memory(ns), 0)
        cm.assert_called_once_with("consolidate")

    def test_syncers_registry(self):
        self.assertEqual(set(self.ma.SYNCERS), {"gmail", "github", "calendar"})
        self.assertTrue(issubclass(self.ma.AutosyncError, RuntimeError))

    def test_run_loop_stops_on_event(self):
        """run_loop exits promptly when the stop event is set."""
        import threading

        stop = threading.Event()
        with patch.object(self.ma, "_sync_due", return_value={}) as due:
            t = threading.Thread(target=self.ma.run_loop, args=(stop,), kwargs={"poll_sec": 1})
            t.start()
            stop.set()
            t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(due.call_count, 0)

    def test_run_loop_kill_switch(self):
        import threading

        with (
            patch.dict("os.environ", {"ZELINE_AUTOSYNC_OFF": "1"}),
            patch.object(self.ma, "_sync_due") as due,
        ):
            self.ma.run_loop(threading.Event(), poll_sec=1)
        due.assert_not_called()


if __name__ == "__main__":
    unittest.main()
