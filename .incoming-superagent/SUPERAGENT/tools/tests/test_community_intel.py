import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import community_intel as CI  # noqa: E402


def _msgs():
    return [
        CI.Message("Kapan claim ZkProtoX dibuka min?", ts=100, reactions=5),
        CI.Message("gas di base mahal ga sih buat farming", ts=200, reactions=2),
        CI.Message("mantap cuan dari airdrop kemarin keren", ts=300),
        CI.Message("ini scam ya? kok dananya gak cair penipuan", ts=400, reactions=8),
        CI.Message("kapan claim dibuka min?", ts=500, reactions=3),
    ]


class CommunityIntelTest(unittest.TestCase):
    def test_topics_extracted(self):
        r = CI.analyze(_msgs(), now=1000, window_hours=24)
        topics = dict(r.top_topics)
        self.assertIn("claim", topics)
        self.assertGreaterEqual(topics["claim"], 2)

    def test_trending_questions(self):
        r = CI.analyze(_msgs(), now=1000, window_hours=24)
        self.assertTrue(r.trending_questions)
        self.assertTrue(any("claim" in q.lower() for q in r.trending_questions))

    def test_fud_detection(self):
        r = CI.analyze(_msgs(), now=1000, window_hours=24)
        self.assertTrue(r.fud_alerts)
        self.assertTrue(any("scam" in f.lower() for f in r.fud_alerts))

    def test_content_ideas_generated(self):
        r = CI.analyze(_msgs(), now=1000, window_hours=24)
        self.assertTrue(r.content_ideas)

    def test_window_filter(self):
        # window 1h before now=500 → only ts>=499-... ensure window_count <= total
        r = CI.analyze(_msgs(), now=500, window_hours=0.05)
        self.assertLessEqual(r.window_count, r.total)

    def test_sentiment_negative(self):
        msgs = [CI.Message("scam rug rugi gagal jelek")]
        r = CI.analyze(msgs)
        self.assertEqual(r.sentiment["label"], "negatif")

    def test_empty_safe(self):
        r = CI.analyze([])
        self.assertEqual(r.total, 0)
        self.assertEqual(r.sentiment["label"], "netral")


if __name__ == "__main__":
    unittest.main()
