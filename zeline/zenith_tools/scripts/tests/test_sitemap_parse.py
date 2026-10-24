import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import sitemap_parse as S  # noqa: E402

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/</loc>
    <lastmod>2026-10-01</lastmod>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://example.com/about</loc>
    <changefreq>weekly</changefreq>
  </url>
</urlset>"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sm1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sm2.xml</loc></sitemap>
</sitemapindex>"""

ROBOTS = """# comment line
User-agent: *
Disallow: /admin
Disallow: /private/
Allow: /private/public

User-agent: Googlebot
Crawl-delay: 2
Disallow:

Sitemap: https://example.com/sitemap.xml
"""


class SitemapTest(unittest.TestCase):
    def test_parse_urls(self):
        entries = S.parse_sitemap(SITEMAP)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["loc"], "https://example.com/")
        self.assertEqual(entries[0]["lastmod"], "2026-10-01")
        self.assertEqual(entries[1]["changefreq"], "weekly")

    def test_index_detected(self):
        self.assertTrue(S.is_sitemap_index(INDEX))
        self.assertFalse(S.is_sitemap_index(SITEMAP))

    def test_parse_index(self):
        self.assertEqual(S.parse_sitemap_index(INDEX),
                         ["https://example.com/sm1.xml", "https://example.com/sm2.xml"])

    def test_prefixed_namespace(self):
        # Real-world documents sometimes bind the sitemap namespace to a
        # prefix instead of the default namespace.
        prefixed = SITEMAP.replace(
            '<urlset xmlns=', '<urlset xmlns:sm="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns='
        ).replace(
            '<url>', '<sm:url>').replace('</url>', '</sm:url>').replace(
            '<loc>', '<sm:loc>').replace('</loc>', '</sm:loc>').replace(
            '<lastmod>', '<sm:lastmod>').replace('</lastmod>', '</sm:lastmod>').replace(
            '<changefreq>', '<sm:changefreq>').replace('</changefreq>', '</sm:changefreq>').replace(
            '<priority>', '<sm:priority>').replace('</priority>', '</sm:priority>')
        entries = S.parse_sitemap(prefixed)
        self.assertTrue(any(e["loc"] == "https://example.com/" for e in entries))


class RobotsTest(unittest.TestCase):
    def test_parse_groups(self):
        robots = S.parse_robots(ROBOTS)
        self.assertIn("*", robots["agents"])
        self.assertIn("googlebot", robots["agents"])
        self.assertEqual(robots["sitemaps"], ["https://example.com/sitemap.xml"])
        disallows = [r["value"] for r in robots["agents"]["*"] if r["key"] == "disallow"]
        self.assertIn("/admin", disallows)

    def test_allowed_paths(self):
        robots = S.parse_robots(ROBOTS)
        self.assertTrue(S.robots_allowed(robots, "/blog/post-1"))
        self.assertFalse(S.robots_allowed(robots, "/admin"))
        self.assertFalse(S.robots_allowed(robots, "/private/secret"))
        self.assertTrue(S.robots_allowed(robots, "/private/public/page"))

    def test_specific_agent_falls_back_to_star(self):
        robots = S.parse_robots(ROBOTS)
        # Bingbot has no group -> falls back to '*'
        self.assertFalse(S.robots_allowed(robots, "/admin", user_agent="bingbot"))

    def test_empty_disallow_means_all_allowed(self):
        robots = S.parse_robots(ROBOTS)
        # Googlebot's group has only an empty Disallow -> everything allowed.
        self.assertTrue(S.robots_allowed(robots, "/admin", user_agent="googlebot"))

    def test_longest_match_wins(self):
        robots = S.parse_robots("""
User-agent: *
Disallow: /
Allow: /public
""")
        self.assertTrue(S.robots_allowed(robots, "/public/x"))
        self.assertFalse(S.robots_allowed(robots, "/private/x"))


if __name__ == "__main__":
    unittest.main()
