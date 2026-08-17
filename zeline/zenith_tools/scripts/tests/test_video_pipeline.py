import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import video_pipeline as V  # noqa: E402


def _brief():
    return V.VideoBrief(topic="3 Airdrop Base Worth Difarming",
                        key_points=["ZkProtoX points", "BaseSwap volume", "Aerodrome LP"],
                        platform="tiktok", duration_sec=45)


class VideoPipelineTest(unittest.TestCase):
    def test_segments_hook_points_cta(self):
        segs = V.build_segments(_brief())
        self.assertEqual(segs[0].label, "hook")
        self.assertEqual(segs[-1].label, "cta")
        self.assertEqual(sum(1 for s in segs if s.label == "point"), 3)

    def test_timeline_contiguous(self):
        segs = V.build_segments(_brief())
        for a, b in zip(segs, segs[1:]):
            self.assertAlmostEqual(a.end, b.start, places=2)
        self.assertAlmostEqual(segs[0].start, 0.0)

    def test_srt_format(self):
        srt = V.to_srt(V.build_segments(_brief()))
        self.assertIn("-->", srt)
        self.assertRegex(srt, r"\d{2}:\d{2}:\d{2},\d{3}")

    def test_package_keys(self):
        pkg = V.build_package(_brief())
        for k in ("topic", "platform", "total_sec", "script", "storyboard",
                  "voiceover_lines", "srt"):
            self.assertIn(k, pkg)
        self.assertEqual(len(pkg["voiceover_lines"]), 5)

    def test_total_duration_close_to_brief(self):
        pkg = V.build_package(_brief())
        self.assertAlmostEqual(pkg["total_sec"], 45.0, delta=1.0)

    def test_min_duration_floor(self):
        b = V.VideoBrief(topic="x", key_points=["a"], duration_sec=2)
        pkg = V.build_package(b)
        self.assertGreaterEqual(pkg["total_sec"], 10.0)


if __name__ == "__main__":
    unittest.main()
