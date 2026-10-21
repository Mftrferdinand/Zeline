"""Private bounded tool-boundary diagnostics, independent of mutation audit."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class RuntimeTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = self.temp.name
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        self.temp.cleanup()

    def test_boundaries_exist_before_work_and_do_not_contain_arguments(self):
        trace = importlib.import_module("zeline.runtime_trace")
        with trace.tool_span("telegram:private-user", "run_shell"):
            rows = [json.loads(line) for line in trace.trace_path().read_text().splitlines()]
            self.assertEqual([r["phase"] for r in rows], ["start"])
        rows = [json.loads(line) for line in trace.trace_path().read_text().splitlines()]
        self.assertEqual([r["phase"] for r in rows], ["start", "end"])
        self.assertEqual(rows[0]["call_id"], rows[1]["call_id"])
        self.assertEqual(rows[1]["outcome"], "completed")
        self.assertGreaterEqual(rows[1]["elapsed_seconds"], 0)
        self.assertNotIn("private-user", trace.trace_path().read_text())
        self.assertNotIn("args", rows[0])
        if os.name == "posix":
            self.assertEqual(trace.trace_path().stat().st_mode & 0o777, 0o600)

    def test_executor_traces_before_dispatch_and_preserves_mutation_audit(self):
        tools = importlib.import_module("zeline.tools")
        trace = importlib.import_module("zeline.runtime_trace")
        executor = tools.ToolExecutor("owner", profile="full", workspace=self.temp.name)
        checked = []

        def dispatch(_name, _args):
            rows = [json.loads(line) for line in trace.trace_path().read_text().splitlines()]
            checked.append(rows[-1]["phase"])
            return "small result"

        with mock.patch.object(executor, "_execute", side_effect=dispatch):
            result = executor.run("read_file", {"path": "secret-name", "secret": "never log me"})
        self.assertEqual(result, "small result")
        self.assertEqual(checked, ["start"])
        self.assertNotIn("secret-name", trace.trace_path().read_text())
        self.assertNotIn("never log me", trace.trace_path().read_text())
        events = importlib.import_module("zeline.events")
        self.assertEqual(events.EventLog().recent("owner"), [])

    def test_start_record_survives_abrupt_disposable_worker_exit(self):
        code = (
            "import os; from zeline.runtime_trace import tool_span; "
            "span=tool_span('owner','search_files'); span.__enter__(); os._exit(0)"
        )
        child = subprocess.run([sys.executable, "-c", code],
                               cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        trace = importlib.import_module("zeline.runtime_trace")
        rows = [json.loads(line) for line in trace.trace_path().read_text().splitlines()]
        self.assertEqual([row["phase"] for row in rows], ["start"])

    def test_exception_is_traced_without_exception_text(self):
        trace = importlib.import_module("zeline.runtime_trace")
        with self.assertRaisesRegex(ValueError, "private credential"):
            with trace.tool_span("owner", "read_file"):
                raise ValueError("private credential")
        text = trace.trace_path().read_text()
        self.assertNotIn("private credential", text)
        row = json.loads(text.splitlines()[-1])
        self.assertEqual(row["outcome"], "raised")
        self.assertEqual(row["exception_type"], "ValueError")

    def test_trace_failure_does_not_prevent_work(self):
        trace = importlib.import_module("zeline.runtime_trace")
        with mock.patch.object(trace, "_append", side_effect=OSError("disk error")):
            with trace.tool_span("owner", "read_file"):
                completed = True
        self.assertTrue(completed)

    def test_rotated_trace_has_bounded_file_count(self):
        trace = importlib.import_module("zeline.runtime_trace")
        with mock.patch.object(trace, "MAX_TRACE_BYTES", 1024):
            for _ in range(12):
                with trace.tool_span("owner", "runtime_info"):
                    pass
        files = list(trace.trace_path().parent.glob("runtime-trace.jsonl*"))
        self.assertLessEqual(len(files), 3)
        self.assertTrue(all(p.stat().st_size <= 1600 for p in files))


if __name__ == "__main__":
    unittest.main()
