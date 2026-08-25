"""Contract tests for optional premium web-search providers + free fallback."""
from __future__ import annotations

import importlib
import os
import unittest
from unittest import mock


class WebProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.wp = importlib.import_module("zeline.web_providers")
        # Ensure no real keys leak in from the environment during tests.
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_no_keys_means_no_premium_available(self):
        with mock.patch.object(self.wp, "_env", return_value=""):
            self.assertEqual(self.wp.available_premium(), [])
            self.assertIsNone(self.wp.search_premium("anything"))

    def test_premium_skipped_when_key_absent_returns_none(self):
        # Even if a provider's search would succeed, an unset key must gate it.
        with mock.patch.object(self.wp, "_env", return_value=""), \
             mock.patch.object(self.wp.TavilyProvider, "search",
                               return_value=[("X", "https://x.com")]) as tav:
            self.assertIsNone(self.wp.search_premium("q"))
        tav.assert_not_called()

    def test_tavily_used_when_key_present(self):
        def fake_env(name):
            return "tvly-test" if name == "TAVILY_API_KEY" else ""
        with mock.patch.object(self.wp, "_env", side_effect=fake_env), \
             mock.patch.object(self.wp.TavilyProvider, "search",
                               return_value=[("Doc", "https://docs.example/")]) as tav:
            out = self.wp.search_premium("query")
        tav.assert_called_once()
        self.assertEqual(out, [("Doc", "https://docs.example/")])

    def test_fallback_order_tavily_then_exa(self):
        # Both keys present, tavily empty -> exa is tried next.
        with mock.patch.object(self.wp, "_env", return_value="key"), \
             mock.patch.object(self.wp.TavilyProvider, "search", return_value=[]), \
             mock.patch.object(self.wp.ExaProvider, "search",
                               return_value=[("E", "https://e.com")]) as exa:
            out = self.wp.search_premium("query")
        exa.assert_called_once()
        self.assertEqual(out, [("E", "https://e.com")])

    def test_all_premium_fail_returns_none_for_free_fallback(self):
        with mock.patch.object(self.wp, "_env", return_value="key"), \
             mock.patch.object(self.wp.SearxngProvider, "search", return_value=[]), \
             mock.patch.object(self.wp.TavilyProvider, "search", return_value=[]), \
             mock.patch.object(self.wp.ExaProvider, "search", return_value=[]), \
             mock.patch.object(self.wp.BraveProvider, "search", return_value=[]):
            self.assertIsNone(self.wp.search_premium("query"))

    def test_searxng_is_first_and_url_gated(self):
        # SearXNG runs before the API providers, gated by SEARXNG_URL not a key.
        self.assertEqual(self.wp.PREMIUM_PROVIDERS[0].name, "searxng")
        self.assertEqual(self.wp.PREMIUM_PROVIDERS[0].env_key, "SEARXNG_URL")

        def fake_env(name):
            return "https://searx.example" if name == "SEARXNG_URL" else ""
        with mock.patch.object(self.wp, "_env", side_effect=fake_env), \
             mock.patch.object(self.wp.SearxngProvider, "search",
                               return_value=[("S", "https://s.com")]) as sx, \
             mock.patch.object(self.wp.TavilyProvider, "search") as tav:
            out = self.wp.search_premium("q")
        sx.assert_called_once()
        tav.assert_not_called()
        self.assertEqual(out, [("S", "https://s.com")])


class WebSearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module("zeline.tools")

    def test_web_search_prefers_premium_when_available(self):
        # When a premium provider returns hits, web_search must use them and
        # NOT fall through to the builtin free chain.
        with mock.patch("zeline.web_providers.search_premium",
                        return_value=[("Prem", "https://prem.example/")]), \
             mock.patch.object(self.tools, "_search_bing_jina") as bing:
            out = self.tools._web_search("something")
        bing.assert_not_called()
        self.assertIn("Prem", out)
        self.assertIn("https://prem.example/", out)

    def test_web_search_falls_back_to_free_chain_without_premium(self):
        # No premium available -> existing free chain (Bing SERP) still works.
        with mock.patch("zeline.web_providers.search_premium", return_value=None), \
             mock.patch.object(self.tools, "_search_bing_jina",
                               return_value=[("FastAPI", "https://fastapi.tiangolo.com/")]) as bing:
            out = self.tools._web_search("fastapi")
        bing.assert_called_once()
        self.assertIn("FastAPI", out)


if __name__ == "__main__":
    unittest.main()
