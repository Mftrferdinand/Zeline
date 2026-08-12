import unittest
from unittest import mock

from zeline import skill_publish


class ScrubTests(unittest.TestCase):
    def test_scrub_replaces_identity(self):
        text = "Hubungi mftrferdinand@gmail.com, chat id 7387183839, dari kedaicloud."
        out, count, samples = skill_publish.scrub(text)
        self.assertNotIn("mftrferdinand@gmail.com", out)
        self.assertNotIn("7387183839", out)
        self.assertNotIn("kedaicloud", out.lower())
        self.assertGreaterEqual(count, 3)
        self.assertTrue(samples)

    def test_scrub_termux_home(self):
        out, _c, _s = skill_publish.scrub("path /data/data/com.termux/files/home/x")
        self.assertNotIn("com.termux", out)
        self.assertIn("~/x", out)


class ScanTests(unittest.TestCase):
    def test_clean_text_no_findings(self):
        text = "# Skill\n> desc\nLangkah 1: pakai key dari operator.\n"
        self.assertEqual(skill_publish.scan_sensitive(text), [])

    def test_detects_openai_key(self):
        text = "api_key = 'sk-abcdefghijklmnopqrstuvwxyz012345'\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 1 for f in findings))

    def test_detects_github_token(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 1 for f in findings))

    def test_detects_leftover_email(self):
        text = "email real@somewhere.org\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 2 and f.label == "Email" for f in findings))

    def test_detects_infra_proxy(self):
        text = "base_url http://localhost:20128/v1\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 3 for f in findings))

    def test_placeholder_not_flagged(self):
        text = "api_key = 'your_api_key_here'\nAuthorization: Bearer <YOUR_TOKEN>\n"
        self.assertEqual(skill_publish.scan_sensitive(text), [])


class PrepareTests(unittest.TestCase):
    def test_prepare_blocks_on_finding(self):
        dirty = "# S\n> d\nsk-abcdefghijklmnopqrstuvwxyz012345\n"
        with mock.patch.object(skill_publish.skills, "load_skill", return_value=dirty):
            plan = skill_publish.prepare("x")
        self.assertFalse(plan.ok)
        self.assertTrue(plan.findings)

    def test_prepare_ok_when_clean(self):
        clean = "# Clean\n> deskripsi aman\nLangkah pakai key dari operator.\n"
        with mock.patch.object(skill_publish.skills, "load_skill", return_value=clean):
            plan = skill_publish.prepare("x")
        self.assertTrue(plan.ok)
        self.assertEqual(plan.findings, [])

    def test_prepare_scrubs_before_scan(self):
        # Email pribadi + chat id: harus discrub jadi bersih, bukan diblokir.
        dirty = "# S\n> d\nkontak mftrferdinand@gmail.com id 7387183839\n"
        with mock.patch.object(skill_publish.skills, "load_skill", return_value=dirty):
            plan = skill_publish.prepare("x")
        self.assertTrue(plan.ok, msg=f"findings: {plan.findings}")
        self.assertGreater(plan.scrub_count, 0)

    def test_prepare_load_error(self):
        with mock.patch.object(skill_publish.skills, "load_skill", return_value="ERROR: skill 'x' tidak ditemukan."):
            plan = skill_publish.prepare("x")
        self.assertFalse(plan.ok)
        self.assertTrue(plan.error)


if __name__ == "__main__":
    unittest.main()
