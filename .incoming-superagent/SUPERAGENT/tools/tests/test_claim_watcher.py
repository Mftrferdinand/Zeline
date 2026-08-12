import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import claim_watcher as CW  # noqa: E402

NOW = 1_700_000_000.0


class ClaimWatcherTest(unittest.TestCase):
    def test_due_fires_at_offset(self):
        w = CW.ClaimWatcher(offsets_h=[48, 2, 0])
        # event 2 jam dari sekarang → harus trigger offset H-2
        w.add(CW.AirdropEvent("X", "claim_open", NOW + 2 * 3600, chain="Base"))
        alerts = w.due(NOW)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].offset_h, 2)
        self.assertIn("CLAIM BUKA", alerts[0].message)

    def test_each_offset_fires_once(self):
        w = CW.ClaimWatcher(offsets_h=[2])
        w.add(CW.AirdropEvent("X", "claim_open", NOW + 2 * 3600))
        self.assertEqual(len(w.due(NOW)), 1)
        self.assertEqual(len(w.due(NOW)), 0)   # gak dobel

    def test_no_alert_when_far(self):
        w = CW.ClaimWatcher(offsets_h=[2, 0])
        w.add(CW.AirdropEvent("X", "claim_open", NOW + 10 * 86400))
        self.assertEqual(w.due(NOW), [])

    def test_upcoming_sorted(self):
        w = CW.ClaimWatcher()
        w.add(CW.AirdropEvent("Far", "snapshot", NOW + 5 * 86400))
        w.add(CW.AirdropEvent("Near", "claim_open", NOW + 1 * 86400))
        up = w.upcoming(NOW, within_days=14)
        self.assertEqual([e.project for e in up], ["Near", "Far"])

    def test_persistence_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "events.json"
            w = CW.ClaimWatcher(store=path)
            w.add(CW.AirdropEvent("X", "vesting_unlock", NOW + 3 * 86400, note="n"))
            w2 = CW.ClaimWatcher(store=path)
            self.assertEqual(len(w2.events), 1)
            self.assertEqual(w2.events[0].project, "X")


if __name__ == "__main__":
    unittest.main()
