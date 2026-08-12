import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import guide_studio as G  # noqa: E402


def _spec():
    return G.GuideSpec(
        project="ZkProtoX", chain="Base",
        referral_url="https://zkprotox.xyz/?ref=af",
        steps=[
            G.GuideStep("Connect wallet", url="https://zkprotox.xyz",
                        note="cek domain", screenshot_hint="connect page"),
            G.GuideStep("Bridge ETH"),
        ])


class GuideStudioTest(unittest.TestCase):
    def test_full_guide_has_title_and_steps(self):
        md = G.build_full_guide(_spec())
        self.assertIn("# Panduan Airdrop ZkProtoX", md)
        self.assertIn("**1.**", md)
        self.assertIn("**2.**", md)
        self.assertIn("Disclaimer", md)

    def test_referral_embedded(self):
        md = G.build_full_guide(_spec())
        self.assertIn("ref=af", md)

    def test_short_telegram_and_x(self):
        tg = G.build_short(_spec(), "telegram")
        x = G.build_short(_spec(), "x")
        self.assertIn("ZkProtoX", tg)
        self.assertIn("#Airdrop", x)
        self.assertIn("2 langkah", tg)

    def test_bundle_keys_and_screenshot_jobs(self):
        b = G.build_bundle(_spec())
        self.assertEqual(set(b), {"full_markdown", "telegram", "x", "screenshot_jobs"})
        self.assertEqual(b["screenshot_jobs"], ["connect page"])

    def test_no_referral_safe(self):
        spec = G.GuideSpec(project="X", chain="Base", steps=[G.GuideStep("a")])
        md = G.build_full_guide(spec)
        self.assertIn("Panduan Airdrop X", md)


if __name__ == "__main__":
    unittest.main()
