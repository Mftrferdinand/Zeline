"""Extractive tool-output compression: critical info must survive.

``_truncate_output`` compresses large tool results deterministically without
an LLM call (zero-token, mirroring ``zeline.compaction``'s philosophy). These
tests pin the contract the owner asked for explicitly: after compression, the
numbers, error messages + tracebacks, file paths, and the final result lines
are still there — and whatever is dropped is marked, never silently cut.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from zeline import config, tools


def _failing_pytest_output(failures=3, passed=597):
    """Realistic pytest run: boilerplate passes around a few real failures."""
    lines = ["$ pytest tests/ -x -q"]
    total = failures + passed
    made = 0
    for i in range(total):
        if made < failures and i % (total // failures) == (total // failures) - 1:
            idx = made
            lines.append(f"tests/test_billing_{idx}.py::test_invoice_total FAILED")
            lines.append("E       assert 142500 == 149000")
            lines.append("E        +  where 142500 = calculate_total(...)")
            lines.append("Traceback (most recent call last):")
            lines.append('  File "/app/src/billing.py", line 87, in calculate_total')
            lines.append('    raise ValueError("negative line item")')
            lines.append("ValueError: negative line item")
            made += 1
        else:
            lines.append(f"tests/test_mod{i}.py::test_case_{i} PASSED [{100 * i // total}%]")
    lines.append(f"{failures} failed, {passed} passed in 42.7s")
    return "\n".join(lines)


def _long_batch_log():
    """Long log with the important numbers buried in the middle."""
    lines = ["2026-10-07 10:00:01 starting batch processor v2.3"]
    for i in range(800):
        lines.append(
            f"2026-10-07 10:{i // 60:02d}:{i % 60:02d} "
            f"INFO worker={i % 8} heartbeat ok queue_depth={1000 - i}"
        )
    lines.insert(400, "2026-10-07 10:06:40 ERROR shard=7 replication lag 48213ms exceeds threshold 5000ms")
    lines.insert(401, "2026-10-07 10:06:41 WARN failover initiated for /data/shard7/wal.log")
    lines.append("2026-10-07 10:14:00 batch complete: processed=982144 failed=3")
    return "\n".join(lines)


class CompressionConstantTests(unittest.TestCase):
    def test_threshold_and_ratio_are_tunable_module_constants(self):
        self.assertGreater(tools.TOOL_OUTPUT_COMPRESS_THRESHOLD, 0)
        self.assertGreater(tools.TOOL_OUTPUT_TARGET_RATIO, 0)
        self.assertLess(tools.TOOL_OUTPUT_TARGET_RATIO, 1)
        self.assertGreater(tools.TOOL_OUTPUT_CONTEXT_LINES, 0)

    def test_target_size_is_threshold_times_ratio(self):
        target = int(tools.TOOL_OUTPUT_COMPRESS_THRESHOLD * tools.TOOL_OUTPUT_TARGET_RATIO)
        self.assertLess(target, tools.TOOL_OUTPUT_COMPRESS_THRESHOLD)


class ExtractiveSummaryTests(unittest.TestCase):
    def _target(self):
        return int(tools.TOOL_OUTPUT_COMPRESS_THRESHOLD * tools.TOOL_OUTPUT_TARGET_RATIO)

    def test_small_output_is_returned_unchanged(self):
        self.assertEqual(tools._truncate_output("hello"), "hello")

    def test_empty_output_message_is_preserved(self):
        self.assertEqual(tools._truncate_output("   "), "(no output)")

    def test_summary_is_deterministic(self):
        text = _long_batch_log()
        first = tools._extractive_summary(text, self._target())
        second = tools._extractive_summary(text, self._target())
        self.assertEqual(first, second)

    def test_summary_stays_within_target(self):
        for text in (_failing_pytest_output(), _long_batch_log(), "s" * 30_000):
            summary = tools._extractive_summary(text, self._target())
            self.assertLessEqual(len(summary), self._target())

    def test_failing_test_run_keeps_critical_info(self):
        summary = tools._extractive_summary(_failing_pytest_output(), self._target())
        for must_keep in (
            "142500",  # assert values
            "149000",
            "Traceback (most recent call last):",  # traceback header
            "ValueError: negative line item",  # error message
            "/app/src/billing.py",  # file path
            "line 87",
            "test_invoice_total",  # failing test id
            "pytest tests/ -x -q",  # head context: what was run
        ):
            with self.subTest(kept=must_keep):
                self.assertIn(must_keep, summary)

    def test_failing_test_run_keeps_final_result_line(self):
        summary = tools._extractive_summary(_failing_pytest_output(), self._target())
        self.assertIn("passed in 42.7s", summary)

    def test_long_log_keeps_middle_numbers_error_and_tail(self):
        summary = tools._extractive_summary(_long_batch_log(), self._target())
        for must_keep in (
            "48213ms",  # key number buried in the middle
            "5000ms",
            "ERROR shard=7 replication lag",  # error line
            "/data/shard7/wal.log",  # file path
            "failover initiated",
            "processed=982144",  # final result numbers
            "failed=3",
            "batch complete",  # tail context
            "starting batch processor v2.3",  # head context
        ):
            with self.subTest(kept=must_keep):
                self.assertIn(must_keep, summary)

    def test_dropped_spans_are_marked_not_silent(self):
        summary = tools._extractive_summary(_long_batch_log(), self._target())
        self.assertIn("lines omitted", summary)

    def test_single_giant_line_is_bounded(self):
        summary = tools._extractive_summary("s" * 30_000, self._target())
        self.assertLessEqual(len(summary), self._target())

    def test_summary_is_pure_text_in_text_out(self):
        # No config, no disk, no randomness: same text always compresses the
        # same way, so background workers can use it without call-site state.
        text = _long_batch_log()
        with mock.patch("zeline.tools.config") as fake_config:
            fake_config.side_effect = AssertionError("must not touch config")
            first = tools._extractive_summary(text, self._target())
        self.assertEqual(first, tools._extractive_summary(text, self._target()))


class TruncateOutputWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        patcher = mock.patch.object(config, "DATA_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_full_text_remains_recoverable_via_offload(self):
        text = _long_batch_log()
        result = tools._truncate_output(text)
        self.assertIn("read_file(", result)
        stored = list(Path(self._tmp.name).rglob("*.txt"))
        self.assertTrue(stored, "full output must be offloaded to disk")
        self.assertEqual(stored[0].read_text(encoding="utf-8"), text.strip())

    def test_compressed_result_is_far_smaller_than_input(self):
        text = _long_batch_log()
        result = tools._truncate_output(text)
        self.assertLess(len(result), len(text) // 3)


if __name__ == "__main__":
    unittest.main()
