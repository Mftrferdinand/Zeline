import os
import unittest
from unittest import mock

from zeline import skill_publish


class ScrubTests(unittest.TestCase):
    """The scrubber must protect WHOEVER installed Zeline, not one person.

    The first version hardcoded the maintainer's email, chat id, project names,
    and the bare word ``aes``. That shipped two bugs to everyone else: their own
    identity was never scrubbed, and ``AES-256-GCM`` came out as
    ``the user-256-GCM`` in any skill that mentioned cryptography.
    """

    def test_it_scrubs_the_home_directory_of_this_machine(self):
        out, count, samples = skill_publish.scrub(f"look in {os.path.expanduser('~')}/project")
        self.assertNotIn(os.path.expanduser("~"), out)
        self.assertIn("~/project", out)
        self.assertGreater(count, 0)
        self.assertTrue(samples)

    def test_termux_home_is_recognised_even_when_home_points_elsewhere(self):
        with mock.patch.dict(os.environ, {"HOME": "/tmp/not-termux"}, clear=False):
            out, _count, _samples = skill_publish.scrub(
                "path /data/data/com.termux/files/home/x"
            )
        self.assertNotIn("com.termux", out)
        self.assertIn("~/x", out)

    def test_it_scrubs_the_owner_identity_from_gateway_config(self):
        saved = {"gateways": {"telegram": {"owner_identity": "555000111", "allowed": ["555000111"]}}}
        with mock.patch.object(skill_publish.config, "stored_config_copy", return_value=saved):
            out, count, _samples = skill_publish.scrub("owner chat id 555000111 only")
        self.assertNotIn("555000111", out)
        self.assertIn("<OWNER_ID>", out)
        self.assertGreater(count, 0)

    def test_operator_declared_terms_are_scrubbed(self):
        saved = {"publish": {"scrub_terms": ["MyCompany", "mybot"]}}
        with mock.patch.object(skill_publish.config, "stored_config_copy", return_value=saved):
            out, _count, _samples = skill_publish.scrub("deploy MyCompany via @mybot now")
        self.assertNotIn("MyCompany", out)
        self.assertNotIn("mybot", out)

    def test_declared_terms_can_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"ZELINE_SCRUB_TERMS": "Acme, widgetco"}, clear=False):
            with mock.patch.object(skill_publish.config, "stored_config_copy", return_value={}):
                out, _count, _samples = skill_publish.scrub("Acme ships widgetco stuff")
        self.assertNotIn("Acme", out)
        self.assertNotIn("widgetco", out)

    def test_it_does_not_mangle_legitimate_technical_text(self):
        """A short scrub term must not eat a word that merely contains it."""
        for text in ("Use AES-256-GCM for envelopes.", "cipher aes-128-cbc", "AESGCM(key)"):
            with self.subTest(text=text):
                with mock.patch.object(skill_publish.config, "stored_config_copy", return_value={}):
                    out, _count, _samples = skill_publish.scrub(text)
                self.assertEqual(out, text)


class ScanTests(unittest.TestCase):
    def test_clean_text_no_findings(self):
        text = "# Skill\n> desc\nLangkah 1: pakai key dari operator.\n"
        self.assertEqual(skill_publish.scan_sensitive(text), [])

    def test_detects_openai_key(self):
        text = "api_key = 'sk-abcdefghijklmnopqrstuvwxyz012345'\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 1 for f in findings))

    def test_detects_github_token(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz01234567\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 1 for f in findings))

    def test_detects_leftover_email(self):
        text = "email real@somewhere.org\n"
        findings = skill_publish.scan_sensitive(text)
        self.assertTrue(any(f.layer == 2 and f.label == "Email" for f in findings))

    def test_detects_any_absolute_home_path_not_just_termux(self):
        """A machine-specific path is a leak on every OS, not only Android."""
        for text in ("/home/alice/project", "/Users/bob/dev", "/data/data/com.termux/files/home"):
            with self.subTest(path=text):
                findings = skill_publish.scan_sensitive(f"run from {text}\n")
                self.assertTrue(
                    any(f.layer == 2 and f.label == "Absolute home path" for f in findings),
                    msg=f"{text} was not flagged",
                )

    def test_detects_any_international_phone_number(self):
        findings = skill_publish.scan_sensitive("call +14155550123 now\n")
        self.assertTrue(any(f.layer == 2 and "Phone" in f.label for f in findings))

    def test_detects_a_loopback_or_lan_endpoint_on_any_port(self):
        """A private endpoint only resolves on the author's machine.

        Pinning one port protected exactly one setup; the shape is what matters.
        """
        for text in ("http://localhost:20128/v1", "http://127.0.0.1:8080", "http://192.168.1.9:3000"):
            with self.subTest(endpoint=text):
                findings = skill_publish.scan_sensitive(f"base_url {text}\n")
                self.assertTrue(any(f.layer == 3 for f in findings), msg=f"{text} not flagged")

    def test_placeholder_not_flagged(self):
        text = "api_key = 'your_api_key_here'\nAuthorization: Bearer <YOUR_TOKEN>"
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
        """A declared term plus the machine's home path must scrub, not block."""
        home = os.path.expanduser("~")
        dirty = f"# S\n> d\nMyCompany ships from {home}/build\n"
        saved = {"publish": {"scrub_terms": ["MyCompany"]}}
        with mock.patch.object(skill_publish.config, "stored_config_copy", return_value=saved), \
             mock.patch.object(skill_publish.skills, "load_skill", return_value=dirty):
            plan = skill_publish.prepare("x")
        self.assertTrue(plan.ok, msg=f"findings: {plan.findings}")
        self.assertGreater(plan.scrub_count, 0)
        self.assertNotIn("MyCompany", plan.scrubbed)

    def test_prepare_load_error(self):
        with mock.patch.object(skill_publish.skills, "load_skill", return_value="ERROR: skill 'x' tidak ditemukan."):
            plan = skill_publish.prepare("x")
        self.assertFalse(plan.ok)
        self.assertTrue(plan.error)


if __name__ == "__main__":
    unittest.main()
