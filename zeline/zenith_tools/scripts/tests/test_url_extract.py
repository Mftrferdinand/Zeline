import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import url_extract as U  # noqa: E402


class IsValidTest(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(U.is_valid("https://example.com/path?q=1"))
        self.assertTrue(U.is_valid("http://localhost:8080/health"))

    def test_invalid(self):
        self.assertFalse(U.is_valid("ftp://example.com"))
        self.assertFalse(U.is_valid("https://"))
        self.assertFalse(U.is_valid("not a url"))
        self.assertFalse(U.is_valid("javascript:alert(1)"))

    def test_control_chars_rejected(self):
        self.assertFalse(U.is_valid("https://exam\tple.com"))


class ExtractTest(unittest.TestCase):
    def test_basic(self):
        text = "see https://a.dev and http://b.io/x now"
        self.assertEqual(U.extract(text), ["https://a.dev", "http://b.io/x"])

    def test_trailing_punctuation_stripped(self):
        text = "go to https://example.com/docs."
        self.assertEqual(U.extract(text), ["https://example.com/docs"])

    def test_parentheses_stripped(self):
        text = "(see https://example.com/a)"
        self.assertEqual(U.extract(text), ["https://example.com/a"])

    def test_dedupe(self):
        text = "https://x.io https://x.io"
        self.assertEqual(U.extract(text), ["https://x.io"])

    def test_extract_valid_filters(self):
        text = "ok https://good.io bad ftp://nope.io"
        self.assertEqual(U.extract_valid(text), ["https://good.io"])

    def test_bare_domains(self):
        text = "check example.com/docs or www.foo.io"
        bare = U.extract_bare(text)
        self.assertIn("https://example.com/docs", bare)
        self.assertIn("https://www.foo.io", bare)


class NormalizeTest(unittest.TestCase):
    def test_drop_tracking(self):
        url = "https://e.io/p?utm_source=x&id=7"
        self.assertEqual(U.normalize(url), "https://e.io/p?id=7")

    def test_keeps_functional_params(self):
        url = "https://github.com/u/r/issues?utm_source=x&q=1"
        self.assertEqual(U.normalize(url), "https://github.com/u/r/issues?utm_source=x&q=1")

    def test_lowercase_host_default_https(self):
        self.assertEqual(U.normalize("HTTP://Example.COM/A"),
                         "http://example.com/A")

    def test_sorts_nothing_drops_nothing_when_clean(self):
        self.assertEqual(U.normalize("https://e.io/p?a=1&b=2"),
                         "https://e.io/p?a=1&b=2")


class DomainsTest(unittest.TestCase):
    def test_unique_lowercased(self):
        text = "https://WWW.Example.com https://example.com/path https://other.io"
        self.assertEqual(U.domains(text), ["example.com", "other.io"])


if __name__ == "__main__":
    unittest.main()
