import pathlib
import sqlite3
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import cost_ledger as C  # noqa: E402


def _mem():
    return C.CostLedger(conn=sqlite3.connect(":memory:"),
                        price_per_1k={"claude": 0.01, "deepseek": 0.0002})


class CostLedgerTest(unittest.TestCase):
    def test_token_usd_estimate(self):
        with _mem() as led:
            usd = led.record_tokens("claude", 1000)
            self.assertAlmostEqual(usd, 0.01, places=6)
            usd2 = led.record_tokens("deepseek", 10000)
            self.assertAlmostEqual(usd2, 0.002, places=6)

    def test_explicit_usd_overrides_estimate(self):
        with _mem() as led:
            usd = led.record_tokens("claude", 1000, usd=0.5)
            self.assertEqual(usd, 0.5)

    def test_summary_aggregates_kinds(self):
        with _mem() as led:
            led.record_tokens("claude", 2000, session_id="s")
            led.record_onchain(1, 100.0, session_id="s")
            led.record_onchain(8453, 50.0, session_id="s")
            led.record_api("opensea", 3, session_id="s")
            s = led.summary()
            self.assertEqual(s.token_total, 2000)
            self.assertAlmostEqual(s.onchain_usd, 150.0, places=4)
            self.assertEqual(s.api_calls, 3)
            self.assertEqual(s.by_chain[1], 100.0)
            self.assertAlmostEqual(s.total_usd, 0.02 + 150.0, places=4)

    def test_session_filter(self):
        with _mem() as led:
            led.record_tokens("claude", 1000, session_id="a")
            led.record_tokens("claude", 5000, session_id="b")
            self.assertEqual(led.summary(session_id="a").token_total, 1000)
            self.assertEqual(led.summary(session_id="b").token_total, 5000)

    def test_report_runs(self):
        with _mem() as led:
            led.record_tokens("claude", 1000)
            self.assertIn("cost ledger", led.summary().report())


if __name__ == "__main__":
    unittest.main()
