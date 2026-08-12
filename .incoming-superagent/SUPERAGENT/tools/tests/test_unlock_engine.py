import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import unlock_engine as U  # noqa: E402

NOW = 1_760_000_000.0


def _mkt():
    return U.MarketState(price_usd=2.0, total_supply=1_000_000_000,
                         circulating_supply=200_000_000, daily_volume_usd=5_000_000)


class UnlockEngineTest(unittest.TestCase):
    def test_big_unlock_high_pressure_and_warning(self):
        ev = U.UnlockEvent("cliff", NOW + 5 * 86400, 8.0)
        v = U.assess_event(ev, _mkt(), NOW)
        self.assertIn(v.pressure, ("high", "extreme"))
        self.assertIn("DULUAN", v.signal)
        self.assertAlmostEqual(v.unlock_value_usd, 160_000_000.0)

    def test_small_unlock_low_pressure(self):
        ev = U.UnlockEvent("tiny", NOW + 200 * 86400, 0.05)
        v = U.assess_event(ev, _mkt(), NOW)
        self.assertEqual(v.pressure, "low")

    def test_past_event_signal(self):
        ev = U.UnlockEvent("done", NOW - 10 * 86400, 5.0)
        v = U.assess_event(ev, _mkt(), NOW)
        self.assertLess(v.days_until, 0)
        self.assertEqual(v.signal, "sudah lewat")

    def test_calendar_sorted_and_filtered(self):
        evs = [U.UnlockEvent("far", NOW + 100 * 86400, 1.0),
               U.UnlockEvent("near", NOW + 10 * 86400, 1.0),
               U.UnlockEvent("beyond", NOW + 1000 * 86400, 1.0)]
        cal = U.build_calendar(evs, _mkt(), NOW, horizon_days=365)
        self.assertEqual([v.label for v in cal], ["near", "far"])

    def test_biggest_pressure(self):
        evs = [U.UnlockEvent("small", NOW + 30 * 86400, 0.1),
               U.UnlockEvent("huge", NOW + 30 * 86400, 10.0)]
        self.assertEqual(U.biggest_pressure(evs, _mkt(), NOW).label, "huge")

    def test_zero_volume_safe(self):
        mkt = U.MarketState(price_usd=1.0, total_supply=1e9,
                            circulating_supply=1e8, daily_volume_usd=0.0)
        v = U.assess_event(U.UnlockEvent("e", NOW + 86400, 1.0), mkt, NOW)
        self.assertEqual(v.pressure, "extreme")


if __name__ == "__main__":
    unittest.main()
