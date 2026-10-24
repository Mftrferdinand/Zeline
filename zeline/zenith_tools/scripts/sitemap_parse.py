#!/usr/bin/env python3
"""
tools/sitemap_parse.py — Parse sitemap.xml & robots.txt Offline  (v1.0)

Web agent yang disuruh "pelajari situs ini" butuh tahu struktur situs:
sitemap.xml kasih daftar URL + prioritas, robots.txt kasih batasan crawl.
Tool ini parse keduanya offline (input = teks yang sudah di-fetch agent
lewat web_fetch/browser), tanpa network dan tanpa dependency.

API:
- parse_sitemap(xml_text) -> list[dict]  (loc, lastmod, changefreq, priority)
- parse_sitemap_index(xml_text) -> list[str]  (URL sitemap anak)
- is_sitemap_index(xml_text) -> bool
- parse_robots(txt) -> dict  (per-user-agent rules, sitemap URLs)
- robots_allowed(robots, path, user_agent="*") -> bool
"""
from __future__ import annotations

import re
from xml.etree import ElementTree

from typing import Optional

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_TAG_RE = re.compile(r"<([\w:]+)>\s*([^<]*?)\s*</\1>")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml_text: str) -> list[dict]:
    """Parse a urlset sitemap into [{loc, lastmod, changefreq, priority}]."""
    entries: list[dict] = []
    try:
        root = ElementTree.fromstring(xml_text)
        nodes = root if _localname(root.tag) == "urlset" else \
            root.findall(".//sm:url", _NS) or root.findall(".//url")
        source = root.iter() if _localname(root.tag) == "urlset" else iter(nodes)
        for url_el in source:
            if _localname(getattr(url_el, "tag", "")) != "url":
                continue
            entry: dict[str, Optional[str]] = {"loc": None, "lastmod": None,
                                               "changefreq": None, "priority": None}
            for child in url_el:
                name = _localname(child.tag)
                if name in entry and child.text:
                    entry[name] = child.text.strip()
            if entry["loc"]:
                entries.append(entry)
    except ElementTree.ParseError:
        # Fallback: regex for minimal/namespace-mangled documents.
        blocks = re.split(r"<(?:\w+:)?url\s*>", xml_text)
        for block in blocks[1:]:
            fields = dict(_TAG_RE.findall(block))
            loc = fields.get("loc") or fields.get("sm:loc")
            if loc:
                entries.append({"loc": loc.strip(), "lastmod": fields.get("lastmod"),
                                "changefreq": fields.get("changefreq"),
                                "priority": fields.get("priority")})
    return entries


def is_sitemap_index(xml_text: str) -> bool:
    """True when the document is a sitemap index (points at child sitemaps)."""
    return "<sitemapindex" in xml_text.lower()


def parse_sitemap_index(xml_text: str) -> list[str]:
    """Child sitemap URLs from a sitemapindex document."""
    urls: list[str] = []
    try:
        root = ElementTree.fromstring(xml_text)
        for el in root.iter():
            if _localname(el.tag) == "loc" and el.text:
                urls.append(el.text.strip())
    except ElementTree.ParseError:
        urls = [v.strip() for k, v in _TAG_RE.findall(xml_text)
                if k.lower().endswith("loc")]
    return urls


def parse_robots(txt: str) -> dict:
    """Parse robots.txt into {user_agents: {agent: [rules]}, sitemaps: [...]}."""
    groups: dict[str, list[dict]] = {}
    current_agents: list[str] = []
    sitemaps: list[str] = []
    for raw_line in (txt or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            current_agents = [value.lower()]
            groups.setdefault(value.lower(), [])
        elif key == "sitemap":
            sitemaps.append(value)
        elif key in ("allow", "disallow", "crawl-delay"):
            if not current_agents:
                # Rules before any User-Agent: treat as '*' per RFC 9309.
                current_agents = ["*"]
                groups.setdefault("*", [])
            groups[current_agents[0]].append({"key": key, "value": value})
    return {"agents": groups, "sitemaps": sitemaps}


def robots_allowed(robots: dict, path: str, user_agent: str = "*") -> bool:
    """Longest-match rule wins (RFC 9309); '*' wildcard supported."""
    path = path or "/"
    ua = user_agent.lower()
    rules = robots.get("agents", {}).get(ua) or robots.get("agents", {}).get("*", [])
    best_len = -1
    allowed = True
    for rule in rules:
        value = rule["value"]
        if rule["key"] == "crawl-delay" or not value:
            continue
        pattern = value.rstrip("*")
        if not pattern or not path.startswith(pattern):
            continue
        if len(pattern) > best_len:
            best_len = len(pattern)
            allowed = rule["key"] == "allow"
        elif len(pattern) == best_len and rule["key"] == "allow":
            # Equal length: Allow wins per RFC 9309.
            allowed = True
    return allowed


def main() -> int:
    """CLI: python3 -m zeline.zenith_tools.scripts.sitemap_parse sitemap.xml"""
    import json
    import sys
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    xml_text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    if is_sitemap_index(xml_text):
        print(json.dumps({"sitemap_index": parse_sitemap_index(xml_text)}, indent=2))
    else:
        print(json.dumps({"urls": parse_sitemap(xml_text)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
