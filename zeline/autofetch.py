"""Auto-fetch background sync (OpenHuman parity).

Periodically fetches data from configured sources into memory — like
OpenHuman's 20-minute sync. Sources: RSS feeds, URLs, files.

Configure via ~/.zeline/autofetch.json:
{
  "interval_minutes": 20,
  "sources": [
    {"type": "rss", "url": "https://...", "name": "Tech news"},
    {"type": "url", "url": "https://...", "name": "Status page"},
    {"type": "file", "path": "/path/to/file", "name": "Notes"}
  ]
}
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path


def _config_path() -> Path:
    return Path.home() / ".zeline" / "autofetch.json"


def _state_path() -> Path:
    return Path.home() / ".zeline" / "autofetch_state.json"


def load_config() -> dict:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return {"interval_minutes": 20, "sources": []}


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {"last_run": 0, "hashes": {}}


def save_state(state: dict) -> None:
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    _state_path().chmod(0o600)


def _fetch_url(url: str, timeout: int = 30) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Zeline/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()[:100000].decode("utf-8", errors="replace")
    except Exception:
        return None


def _fetch_rss(url: str) -> str | None:
    content = _fetch_url(url)
    if not content:
        return None
    # Simple RSS parsing: extract titles
    import re
    titles = re.findall(r"<title>([^<]+)</title>", content)[:20]
    return "\n".join(f"- {t.strip()}" for t in titles[1:] if t.strip())  # skip channel title


def fetch_source(source: dict) -> tuple[str | None, bool]:
    """Fetch a source. Returns (content, changed)."""
    stype = source.get("type", "url")
    name = source.get("name", "unnamed")
    content = None
    if stype == "rss":
        content = _fetch_rss(source.get("url", ""))
    elif stype == "url":
        content = _fetch_url(source.get("url", ""))
        if content:
            content = content[:5000]  # truncate
    elif stype == "file":
        try:
            p = Path(source.get("path", "")).expanduser()
            if p.is_file():
                content = p.read_text(encoding="utf-8", errors="replace")[:5000]
        except Exception:
            pass
    if content is None:
        return None, False
    # Check if changed
    h = hashlib.md5(content.encode()).hexdigest()
    state = load_state()
    old_h = state.get("hashes", {}).get(name)
    if old_h == h:
        return content, False  # unchanged
    state.setdefault("hashes", {})[name] = h
    save_state(state)
    return content, True


def run_sync() -> list[dict]:
    """Run one sync cycle. Returns list of {name, changed, preview}."""
    config = load_config()
    results = []
    for src in config.get("sources", []):
        name = src.get("name", "unnamed")
        content, changed = fetch_source(src)
        if content is not None and changed:
            # Store into episodic memory (with injection filtering - M2 fix)
            try:
                from zeline import memory as _mem
                from zeline import injection_filter as _if
                _filtered = _if.filter_tool_result(content[:2000])
                _mem.add_episode(
                    "cli:autofetch",
                    f"Auto-fetch: {name}",
                    [{"text": f"Source {name} updated:\n{_filtered}"}],
                )
            except Exception:
                pass
            results.append({
                "name": name,
                "changed": True,
                "preview": content[:200],
            })
        elif content is not None:
            results.append({"name": name, "changed": False, "preview": ""})
    state = load_state()
    state["last_run"] = time.time()
    save_state(state)
    return results


def should_run() -> bool:
    """Check if it's time for another sync."""
    config = load_config()
    if not config.get("sources"):
        return False
    interval = config.get("interval_minutes", 20) * 60
    state = load_state()
    return (time.time() - state.get("last_run", 0)) >= interval
