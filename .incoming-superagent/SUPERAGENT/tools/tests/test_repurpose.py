import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import repurpose as R  # noqa: E402


def _src():
    return R.SourceContent(
        title="3 Airdrop Base Worth Difarming",
        key_points=["ZkProtoX points aktif", "BaseSwap volume tinggi", "Aerodrome LP gede"],
        cta="Cek panduan di channel.", referral_url="https://airdropfinder.id",
        hashtags=["airdrop", "base"])


class RepurposeTest(unittest.TestCase):
    def test_x_thread_within_limit(self):
        thread = R.to_x_thread(_src())
        self.assertTrue(all(len(t) <= R.X_LIMIT for t in thread))
        self.assertGreaterEqual(len(thread), 5)  # hook + 3 points + closing

    def test_x_thread_clips_long_points(self):
        src = R.SourceContent(title="t", key_points=["x" * 500])
        thread = R.to_x_thread(src)
        self.assertTrue(all(len(t) <= R.X_LIMIT for t in thread))

    def test_telegram_has_bullets_and_ref(self):
        tg = R.to_telegram(_src())
        self.assertIn("•", tg)
        self.assertIn("airdropfinder.id", tg)

    def test_ig_carousel_structure(self):
        slides = R.to_ig_carousel(_src())
        self.assertEqual(slides[0]["type"], "cover")
        self.assertEqual(slides[-1]["type"], "cta")
        self.assertEqual(len(slides), 5)  # cover + 3 points + cta

    def test_tiktok_script(self):
        t = R.to_tiktok_script(_src())
        self.assertIn("hook", t)
        self.assertEqual(len(t["body"]), 3)
        self.assertGreater(t["est_seconds"], 0)

    def test_youtube_script(self):
        y = R.to_youtube_script(_src())
        self.assertEqual(len(y["segments"]), 3)
        self.assertIn("airdropfinder.id", y["outro"])

    def test_repurpose_all_keys(self):
        out = R.repurpose_all(_src())
        self.assertEqual(set(out), {"x_thread", "telegram", "ig_carousel", "tiktok", "youtube"})


if __name__ == "__main__":
    unittest.main()
