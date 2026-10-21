"""Tool adapters forward the invocation's immutable cancellation control."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock


class FileToolRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = self.temp.name
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.tools = importlib.import_module("zeline.tools")
        self.executor = self.tools.ToolExecutor("owner", "full", self.temp.name)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old
        self.temp.cleanup()

    def test_search_parameters_reach_real_executor_handler(self):
        from pathlib import Path
        (Path(self.temp.name) / 'rows').write_text('needle one\nneedle two\nneedle three\n')
        result = self.executor.run('search_files', {'query': 'needle', 'offset': 1, 'limit': 1})
        self.assertIn('rows:2: needle two', result)
        self.assertNotIn('rows:1:', result)
        self.assertIn('next_offset=2', result)

    def test_file_handlers_receive_the_pinned_stop_predicate(self):
        for name, helper, args in (
            ("read_file", "_read_file", {"path": "note.txt"}),
            ("search_files", "_search_files", {"query": "needle"}),
        ):
            cancelled = [False]
            original = lambda: cancelled[0]
            self.executor._should_stop = original
            seen = []

            def inspect(*_args, **kwargs):
                self.executor._should_stop = lambda: False
                cancelled[0] = True
                callback = kwargs.get("should_stop")
                seen.append(bool(callback and callback()))
                return "result"

            with mock.patch.object(self.tools, helper, side_effect=inspect):
                self.assertEqual(self.executor.run(name, args), "result")
            self.assertEqual(seen, [True], name)


if __name__ == "__main__":
    unittest.main()
