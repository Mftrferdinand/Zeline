import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import secret_redactor as S  # noqa: E402

# Fake but format-valid tokens (revoked / never real).
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SK = "sk-proj-" + "x1Y2z3A4b5C6d7E8f9G0h1I2j3K4l5M6"


class FindSecretsTest(unittest.TestCase):
    def test_github_pat(self):
        hits = S.find_secrets(f"token is {GH} here")
        self.assertEqual([h.kind for h in hits], ["github_pat"])

    def test_openai_key(self):
        hits = S.find_secrets(f"key: {SK}")
        self.assertEqual(hits[0].kind, "openai")

    def test_no_false_positive_on_normal_text(self):
        text = "The skunks crossed the road at sk-8 level, password policy updated."
        self.assertEqual(S.find_secrets(text), [])

    def test_private_key_header(self):
        hits = S.find_secrets("-----BEGIN RSA PRIVATE KEY-----\nMIIEpA")
        self.assertEqual(hits[0].kind, "private_key_block")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
        hits = S.find_secrets(f"auth {jwt}")
        self.assertEqual(hits[0].kind, "jwt")

    def test_overlapping_patterns_deduped(self):
        # password_kv would match the span already covered by github_pat
        text = f"api_key = '{GH}'"
        kinds = [h.kind for h in S.find_secrets(text)]
        self.assertEqual(kinds.count("github_pat"), 1)
        self.assertNotIn("password_kv", kinds)

    def test_sorted_by_position(self):
        text = f"b={SK} a={GH}"
        hits = S.find_secrets(text)
        self.assertTrue(hits[0].start < hits[1].start)


class RedactTest(unittest.TestCase):
    def test_redact_removes_secret(self):
        clean, hits = S.redact(f"curl -H 'Authorization: Bearer {GH}'")
        self.assertNotIn(GH, clean)
        self.assertIn("REDACTED](github_pat)", clean)
        self.assertEqual(len(hits), 1)

    def test_clean_text_untouched(self):
        clean, hits = S.redact("nothing secret here")
        self.assertEqual(clean, "nothing secret here")
        self.assertEqual(hits, [])

    def test_multiple_secrets(self):
        clean, hits = S.redact(f"{GH} and {SK}")
        self.assertNotIn(GH, clean)
        self.assertNotIn(SK, clean)
        self.assertEqual(len(hits), 2)

    def test_hit_never_carries_secret(self):
        _clean, hits = S.redact(f"secret {SK}")
        for hit in hits:
            self.assertNotIn(SK, repr(hit))


class ReportTest(unittest.TestCase):
    def test_report_counts(self):
        r = S.report(f"{GH} {GH} {SK}")
        self.assertEqual(r["secrets_found"], 3)
        self.assertEqual(r["by_kind"]["github_pat"], 2)
        self.assertTrue(r["redacted"])

    def test_report_clean(self):
        r = S.report("all clear")
        self.assertEqual(r["secrets_found"], 0)
        self.assertFalse(r["redacted"])


if __name__ == "__main__":
    unittest.main()
