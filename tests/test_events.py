"""Append-only provenance log for mutating tool calls.

The audit's top self-improvement gap: when a turn dies AFTER the model has
already written a file, saved a skill, or stored a memory, the mutation happened
but the turn transcript can be lost — so there is no record of *what changed,
from which session, and whether it succeeded*. That makes silent state drift
impossible to audit and reflection impossible to roll back.

These tests pin an append-only event log that:

- records every MUTATING tool call (writes, skill/memory edits, shell, tasks)
  with tool name, ok/error status, a short redacted detail, and a timestamp;
- never records read-only tools (no noise from read_file/web_search/list_*);
- degrades instead of raising, exactly like usage_stats — losing an audit row
  must never cost the user their answer;
- hashes identity like memory/sessions so chat IDs never land in the table;
- survives a turn that raises: the event is written at tool time, not at the
  end of a successful turn.
"""
from __future__ import annotations

import importlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    return (
        importlib.import_module("zeline.events"),
        importlib.import_module("zeline.tools"),
    )


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("ZELINE_HOME")
        self.old_key = os.environ.pop("ZELINE_API_KEY", None)
        self.events, self.tools = _fresh(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        if self.old_key is not None:
            os.environ["ZELINE_API_KEY"] = self.old_key
        self.temp.cleanup()

    # ------------------------------------------------------------- core store
    def test_records_are_appended_and_read_back_newest_first(self):
        log = self.events.EventLog()
        log.record("telegram:1", "write_file", "ok", detail="a.py")
        log.record("telegram:1", "manage_skill", "ok", detail="create demo")
        rows = log.recent("telegram:1")
        self.assertEqual([r["tool"] for r in rows], ["manage_skill", "write_file"])
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["detail"], "create demo")
        self.assertTrue(all(r["ts"] > 0 for r in rows))

    def test_events_are_isolated_per_identity_and_identity_is_hashed(self):
        log = self.events.EventLog()
        log.record("telegram:100", "write_file", "ok", detail="x.py")
        self.assertEqual(log.recent("telegram:999"), [])
        raw = log.path.read_bytes()
        self.assertNotIn(b"telegram:100", raw)

    def test_recording_degrades_instead_of_raising(self):
        # A locked or unwritable DB must lose an audit row, never raise. Simulate
        # it the portable way usage_stats does — mock _connect to fail — instead
        # of chmod tricks that behave differently on Windows.
        import sqlite3

        log = self.events.EventLog()
        with mock.patch.object(log, "_connect", side_effect=sqlite3.OperationalError("locked")):
            self.assertFalse(log.record("telegram:1", "write_file", "ok"))
            self.assertEqual(log.recent("telegram:1"), [])

    def test_db_file_is_owner_only(self):
        log = self.events.EventLog()
        log.record("telegram:1", "write_file", "ok")
        # POSIX-only: Windows does not express file mode as 0o600.
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(log.path.stat().st_mode), 0o600)

    def test_counts_group_by_tool(self):
        log = self.events.EventLog()
        log.record("telegram:1", "write_file", "ok")
        log.record("telegram:1", "write_file", "error")
        log.record("telegram:1", "add_memory", "ok")
        counts = log.counts("telegram:1")
        self.assertEqual(counts["write_file"], 2)
        self.assertEqual(counts["add_memory"], 1)

    # --------------------------------------------------------- executor wiring
    def test_mutating_tool_call_is_logged_with_status(self):
        home = self.home / "ws"
        home.mkdir(parents=True, exist_ok=True)
        ex = self.tools.ToolExecutor("telegram:owner", profile="full", workspace=str(home))
        ex.run("write_file", {"path": "note.txt", "content": "hi"})
        log = self.events.EventLog()
        rows = log.recent("telegram:owner")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "write_file")
        self.assertEqual(rows[0]["status"], "ok")

    def test_failed_mutation_is_logged_as_error(self):
        home = self.home / "ws2"
        home.mkdir(parents=True, exist_ok=True)
        ex = self.tools.ToolExecutor("telegram:owner", profile="full", workspace=str(home))
        # edit_file on a missing file returns ERROR; the attempt must still be
        # recorded so a failed side effect is visible, not silently gone.
        ex.run("edit_file", {"path": "missing.txt", "old_text": "a", "new_text": "b"})
        rows = self.events.EventLog().recent("telegram:owner")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "edit_file")
        self.assertEqual(rows[0]["status"], "error")

    def test_readonly_tool_call_is_not_logged(self):
        ex = self.tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        ex.run("list_memory", {})
        ex.run("runtime_info", {})
        self.assertEqual(self.events.EventLog().recent("telegram:owner"), [])

    def test_memory_write_is_logged_as_side_effect(self):
        ex = self.tools.ToolExecutor("telegram:owner", profile="safe", workspace=str(self.home))
        ex.run("add_memory", {"fact": "User prefers dark mode"})
        rows = self.events.EventLog().recent("telegram:owner")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool"], "add_memory")


if __name__ == "__main__":
    unittest.main()
