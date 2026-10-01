#!/usr/bin/env python3
"""Fetch latest airdrops from airdrops.io WordPress REST API.

Usage: python3 scripts/fetch-latest-airdrops.py [days_back]

Outputs a markdown-formatted list of airdrops added in the last N days.
Default: 7 days back.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 7
SINCE = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
API = 'https://airdrops.io/wp-json/wp/v2/airdrop?per_page=50&_fields=title,link,date,date_gmt'

req = Request(API, headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

try:
    resp = urlopen(req, timeout=15)
    data = json.loads(resp.read())
except URLError as e:
    print(f"Error fetching data: {e}", file=sys.stderr)
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"Invalid JSON response: {e}", file=sys.stderr)
    sys.exit(1)

recent = []
for airdrop in data:
    added = datetime.fromisoformat((airdrop.get('date_gmt') or airdrop['date']).replace('Z', '+00:00'))
    if added.tzinfo is None:
        added = added.replace(tzinfo=timezone.utc)
    if added >= SINCE:
        recent.append(airdrop)

if not recent:
    print(f"Tidak ada airdrop baru dalam {DAYS_BACK} hari terakhir.")
    sys.exit(0)

# Sort by date descending (latest first)
recent.sort(key=lambda x: x['date'], reverse=True)

print(f"# Airdrop Terbaru ({len(recent)} dalam {DAYS_BACK} hari)\n")
for a in recent:
    title = a['title']['rendered']
    link = a['link']
    date = a['date'][:10]
    print(f"- **{title}** — [{link}]({link}) _(Added: {date})_")
