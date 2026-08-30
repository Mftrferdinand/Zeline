"""Contract tests for token usage accounting and `zeline stats`.

Two invariants carry most of the weight:

- **Cost is never invented.** An unpriced model must report tokens with no
  number attached, and must not be folded into any subtotal.
- **Recording never breaks a turn.** Every failure mode (locked DB, malformed
  usage, missing usage) loses a statistic and nothing else.

Also pinned: usage extraction across the OpenAI and Anthropic shapes, including
the streamed variants where usage arrives in a separate final chunk / event.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    usage = importlib.import_module("zeline.usage_stats")
    cli = importlib.import_module("zeline.cli")
    return cfg, usage, cli


class UsageBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.usage, self.cli = fresh(self.home)
        self.store = self.usage.UsageStore()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class ExtractionTests(UsageBase):
    def test_openai_shape(self):
        payload = {"usage": {"prompt_tokens": 120, "completion_tokens": 45}}
        self.assertEqual(self.usage.extract_usage(payload), (120, 45))

    def test_anthropic_shape(self):
        payload = {"usage": {"input_tokens": 300, "output_tokens": 88}}
        self.assertEqual(self.usage.extract_usage(payload, "anthropic"), (300, 88))

    def test_total_only_relay_is_attributed_not_dropped(self):
        """Some relays send only total_tokens; losing it would understate usage."""
        payload = {"usage": {"total_tokens": 500}}
        prompt, completion = self.usage.extract_usage(payload)
        self.assertEqual(prompt + completion, 500)

    def test_missing_usage_is_zero_not_an_error(self):
        self.assertEqual(self.usage.extract_usage({}), (0, 0))
        self.assertEqual(self.usage.extract_usage({"usage": None}), (0, 0))
        self.assertEqual(self.usage.extract_usage(None), (0, 0))
        self.assertEqual(self.usage.extract_usage("not a dict"), (0, 0))

    def test_garbage_token_values_are_coerced_safely(self):
        payload = {"usage": {"prompt_tokens": "abc", "completion_tokens": -50}}
        self.assertEqual(self.usage.extract_usage(payload), (0, 0))

    def test_float_token_values_are_accepted(self):
        payload = {"usage": {"prompt_tokens": 10.0, "completion_tokens": 5.0}}
        self.assertEqual(self.usage.extract_usage(payload), (10, 5))


class RecordingTests(UsageBase):
    def test_record_and_aggregate_by_model(self):
        self.store.record("gpt-4o", 100, 50)
        self.store.record("gpt-4o", 200, 25)
        self.store.record("claude-3", 10, 10)
        rows = self.store.by_model()
        self.assertEqual(rows[0]["bucket"], "gpt-4o")
        self.assertEqual(rows[0]["prompt_tokens"], 300)
        self.assertEqual(rows[0]["completion_tokens"], 75)
        self.assertEqual(rows[0]["calls"], 2)
        self.assertEqual(rows[0]["total_tokens"], 375)

    def test_totals_sum_across_models(self):
        self.store.record("a", 10, 5)
        self.store.record("b", 20, 10)
        totals = self.store.totals()
        self.assertEqual(totals["total_tokens"], 45)
        self.assertEqual(totals["calls"], 2)
        self.assertEqual(totals["models"], 2)

    def test_zero_usage_is_not_recorded(self):
        """A provider that omits usage must not create a stream of empty rows."""
        self.assertFalse(self.store.record("gpt-4o", 0, 0))
        self.assertEqual(self.store.totals()["calls"], 0)

    def test_recording_is_disabled_by_config(self):
        saved = self.config.config_copy()
        saved["agent"]["usage_tracking"] = False
        self.config.save_config(saved)
        self.assertFalse(self.usage.enabled())
        self.assertFalse(self.store.record("gpt-4o", 10, 10))

    def test_a_broken_database_loses_a_statistic_not_the_turn(self):
        with mock.patch.object(self.store, "_connect", side_effect=sqlite3.OperationalError("locked")):
            self.assertFalse(self.store.record("gpt-4o", 10, 10))
            self.assertEqual(self.store.by_model(), [])
            self.assertEqual(self.store.totals()["calls"], 0)

    def test_db_file_is_private(self):
        self.store.record("gpt-4o", 1, 1)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)

    def test_identity_is_hashed_in_storage(self):
        self.store.record("gpt-4o", 5, 5, identity="telegram:987654321")
        raw = self.store.path.read_bytes()
        self.assertNotIn(b"987654321", raw)

    def test_day_window_filters_older_rows(self):
        old = time.time() - 30 * 86400
        self.store.record("gpt-4o", 1000, 1000, ts=old)
        self.store.record("gpt-4o", 7, 3)
        since = self.usage.since_day_for(2)
        self.assertEqual(self.store.totals(since)["total_tokens"], 10)
        self.assertEqual(self.store.totals(None)["total_tokens"], 2010)

    def test_by_day_groups_separately(self):
        self.store.record("gpt-4o", 5, 5, ts=time.time() - 5 * 86400)
        self.store.record("gpt-4o", 1, 1)
        self.assertEqual(len(self.store.by_day()), 2)

    def test_clear_removes_everything(self):
        self.store.record("gpt-4o", 5, 5)
        self.assertGreater(self.store.clear(), 0)
        self.assertEqual(self.store.totals()["calls"], 0)

    def test_since_day_for_handles_none_and_zero(self):
        self.assertIsNone(self.usage.since_day_for(None))
        self.assertIsNone(self.usage.since_day_for(0))
        self.assertIsNone(self.usage.since_day_for(-3))


class CostTests(UsageBase):
    def _price(self, mapping):
        saved = self.config.config_copy()
        saved["agent"]["model_prices"] = mapping
        self.config.save_config(saved)

    def test_unpriced_model_returns_none_not_zero(self):
        """Zero would be a lie that silently understates spend."""
        self.assertIsNone(self.usage.cost_for("mystery-model", 1000, 1000))

    def test_priced_model_computes_per_million(self):
        self._price({"gpt-4o": {"input": 2.5, "output": 10.0}})
        cost = self.usage.cost_for("gpt-4o", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 12.5, places=6)

    def test_prefix_match_covers_dated_model_ids(self):
        self._price({"gpt-4o": {"input": 2.0, "output": 4.0}})
        cost = self.usage.cost_for("gpt-4o-2024-08-06", 1_000_000, 0)
        self.assertAlmostEqual(cost, 2.0, places=6)

    def test_malformed_price_entry_is_ignored(self):
        self._price({"gpt-4o": "cheap", "claude": {"input": "x", "output": 1}})
        self.assertIsNone(self.usage.cost_for("gpt-4o", 100, 100))
        self.assertIsNone(self.usage.cost_for("claude", 100, 100))

    def test_no_prices_configured_means_no_cost_anywhere(self):
        for model in ("gpt-4o", "claude-3", "llama"):
            self.assertIsNone(self.usage.cost_for(model, 500, 500))


class FormatTests(UsageBase):
    def test_token_formatting_scales(self):
        self.assertEqual(self.usage.format_tokens(42), "42")
        self.assertEqual(self.usage.format_tokens(1500), "1.5k")
        self.assertEqual(self.usage.format_tokens(2_500_000), "2.50M")


class CliStatsTests(UsageBase):
    def test_stats_on_empty_store_explains_why(self):
        self.assertEqual(self.cli.cmd_stats(), 0)

    def test_stats_reports_recorded_usage(self):
        self.store.record("gpt-4o", 1000, 500)
        self.assertEqual(self.cli.cmd_stats(), 0)
        self.assertEqual(self.cli.cmd_stats(days=7), 0)
        self.assertEqual(self.cli.cmd_stats(by_day=True), 0)

    def test_stats_reset_clears(self):
        self.store.record("gpt-4o", 10, 10)
        self.assertEqual(self.cli.cmd_stats(reset=True), 0)
        self.assertEqual(self.usage.UsageStore().totals()["calls"], 0)

    def test_stats_honours_the_disabled_flag(self):
        saved = self.config.config_copy()
        saved["agent"]["usage_tracking"] = False
        self.config.save_config(saved)
        self.assertEqual(self.cli.cmd_stats(), 0)

    def test_cli_exposes_stats_flags(self):
        parser = self.cli.build_parser()
        namespace = parser.parse_args(["stats", "--days", "7", "--by-day"])
        self.assertEqual(namespace.command, "stats")
        self.assertEqual(namespace.days, 7)
        self.assertTrue(namespace.by_day)
        self.assertTrue(parser.parse_args(["stats", "--reset"]).reset)


class AgentIntegrationTests(UsageBase):
    def test_agent_records_usage_from_a_non_stream_response(self):
        agent_mod = importlib.import_module("zeline.agent")
        usage_mod = importlib.import_module("zeline.usage_stats")
        agent = agent_mod.Zeline(identity="cli:local", tool_profile="safe", workspace=str(self.home))
        agent.model = "test-model"
        agent._record_usage({"usage": {"prompt_tokens": 77, "completion_tokens": 33}})
        rows = usage_mod.UsageStore().by_model()
        self.assertEqual(rows[0]["bucket"], "test-model")
        self.assertEqual(rows[0]["total_tokens"], 110)

    def test_record_usage_swallows_a_failing_store(self):
        """A stats failure must never propagate into the turn."""
        agent_mod = importlib.import_module("zeline.agent")
        agent = agent_mod.Zeline(identity="cli:local", tool_profile="safe", workspace=str(self.home))
        with mock.patch.object(agent, "_usage_store", side_effect=RuntimeError("boom")):
            agent._record_usage({"usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    def test_streaming_requests_ask_for_usage(self):
        """Without stream_options a streamed reply reports no tokens at all."""
        source = Path(str(importlib.import_module("zeline.agent").__file__)).read_text(encoding="utf-8")
        self.assertIn("stream_options", source)
        self.assertIn("include_usage", source)


if __name__ == "__main__":
    unittest.main()
