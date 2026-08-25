"""Contract tests for the reader-proxy search retry + generous timeout."""
from __future__ import annotations

import importlib
import unittest
from unittest import mock


class ReaderRetryTests(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module("zeline.tools")

    def test_reader_uses_generous_timeout(self):
        # Reader-proxy searches must use the longer READER_SEARCH_TIMEOUT, not
        # the tight SEARCH_TIMEOUT (which caused 0-result-while-engine-alive).
        self.assertGreater(self.tools.READER_SEARCH_TIMEOUT[1], self.tools.SEARCH_TIMEOUT[1])

    def test_reader_get_retries_once_on_timeout_then_succeeds(self):
        import requests
        ok = mock.Mock(ok=True, text="Title: x\n\n## [Doc](https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9kb2NzLnB5dGhvbi5vcmcv&x=1)")
        with mock.patch.object(self.tools.requests, "get",
                               side_effect=[requests.ConnectTimeout("cold"), ok]) as g:
            resp = self.tools._reader_get("https://www.bing.com/search?q=python")
        self.assertEqual(g.call_count, 2)
        self.assertIs(resp, ok)

    def test_reader_get_returns_none_when_all_attempts_empty(self):
        empty = mock.Mock(ok=True, text="   ")
        with mock.patch.object(self.tools.requests, "get", return_value=empty) as g:
            resp = self.tools._reader_get("https://www.bing.com/search?q=x")
        self.assertEqual(g.call_count, 2)
        self.assertIsNone(resp)

    def test_bing_jina_parses_results_via_reader_get(self):
        # _search_bing_jina must route through _reader_get and still parse hits.
        fake = mock.Mock(ok=True, text=(
            "About 34,400 results\n\n"
            "1. ## [asyncio docs](https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9kb2NzLnB5dGhvbi5vcmcvMy9saWJyYXJ5L2FzeW5jaW8uaHRtbA&x=1)\n"
        ))
        with mock.patch.object(self.tools, "_reader_get", return_value=fake):
            out = self.tools._search_bing_jina("python asyncio")
        self.assertTrue(out)
        self.assertEqual(out[0][0], "asyncio docs")
        self.assertTrue(out[0][1].startswith("http"))


if __name__ == "__main__":
    unittest.main()
