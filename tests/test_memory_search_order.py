"""Regression tests for memory search determinism.

``memory.search`` sorts by ``(overlap, updated_at)`` descending. Two records
written within the same clock tick can share ``updated_at``; Python's
``list.sort`` is stable, so on identical keys the storage order must be kept.
The previous code fed the raw key to ``sort(reverse=True)`` which also
*reverses* equal-key relative order — turning an otherwise deterministic
result into insertion-order-reversed. These tests pin the documented,
deterministic behaviour without depending on wall-clock resolution.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)


class SearchDeterminismTests(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = tempfile.mkdtemp()
        from zeline import memory as memory_module

        self.memory = memory_module

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old_home

    def test_identical_keys_keep_storage_order(self):
        store = self.memory.MemoryStore("test:tie-order")
        with mock.patch.object(self.memory.time, "time", return_value=1000.0):
            store.add("alpha likes apples")
            store.add("beta likes apples")
        texts = [record["text"] for record in store.search("likes apples")]
        self.assertEqual(texts, ["alpha likes apples", "beta likes apples"])

    def test_higher_recency_still_sorts_first(self):
        store = self.memory.MemoryStore("test:recency-order")
        store.add("alpha likes apples")
        store.add("beta likes apples")
        texts = [record["text"] for record in store.search("likes apples")]
        self.assertEqual(texts, ["beta likes apples", "alpha likes apples"])


if __name__ == "__main__":
    unittest.main()
