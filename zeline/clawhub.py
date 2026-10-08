"""ClawHub skills importer for Zeline.

ClawHub (clawhub.ai) is a public skills registry with 5000+ community skills
in SKILL.md format (AgentSkills spec) — the same format Zeline uses.

Public API (no auth required):
- GET /api/v1/skills?q=<query>&limit=<n> — search
- GET /api/v1/skills/<slug> — skill details (includes SKILL.md content)

All imports go through Zeline's safety scanner before install.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

_CLAWHUB_API = "https://clawhub.ai/api/v1"

# Max ClawHub API response size we'll parse (10MB). [CH2]
_CLAWHUB_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


def _api_get(path: str, params: dict | None = None) -> dict:
    """GET from ClawHub API with SSRF protection."""
    import json as _json

    from zeline.tools import _safe_request

    url = f"{_CLAWHUB_API}{path}"
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"
    # A2-L2: stream the body and enforce the size cap while reading.
    # A lying/missing Content-Length header must not bypass the guard —
    # accumulate chunks and abort past 10MB instead of resp.json().
    resp = _safe_request("GET", url, timeout=30,
                         headers={"User-Agent": "Zeline/1.0"},
                         stream=True)
    resp.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > _CLAWHUB_MAX_RESPONSE_BYTES:
            raise ValueError(
                f"ClawHub response too large (>{_CLAWHUB_MAX_RESPONSE_BYTES} bytes)"
            )
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        return _json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"ClawHub returned invalid JSON: {exc}")


def search_clawhub(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search ClawHub skills. Returns list of {slug, displayName, summary, installs, stars}."""
    limit = max(1, min(limit, 50))
    data = _api_get("/skills", {"q": query, "limit": limit})
    results = []
    for item in data.get("items", []):
        stats = item.get("stats", {})
        results.append({
            "slug": item.get("slug", ""),
            "displayName": item.get("displayName", ""),
            "summary": item.get("summary", ""),
            "installs": stats.get("installs", 0),
            "stars": stats.get("stars", 0),
            "version": (item.get("latestVersion") or {}).get("version", ""),
        })
    # Sort by installs (popularity)
    results.sort(key=lambda x: x["installs"], reverse=True)
    return results


def get_clawhub_skill(slug: str) -> dict[str, Any]:
    """Get full skill details including SKILL.md content."""
    # Sanitize slug
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", slug)[:128]
    if not slug:
        raise ValueError("Invalid slug")
    data = _api_get(f"/skills/{quote(slug)}")
    skill = data.get("skill", {})
    return {
        "slug": skill.get("slug", slug),
        "displayName": skill.get("displayName", ""),
        "summary": skill.get("summary", ""),
        "skill_md": skill.get("description", ""),  # Full SKILL.md content
        "version": (skill.get("latestVersion") or {}).get("version", ""),
    }


def install_clawhub_skill(slug: str) -> str:
    """Download a ClawHub skill and install it into Zeline's learned skills.

    Goes through Zeline's safety scanner before install.
    Returns the installed skill path.
    """
    from zeline import learning
    from zeline import skill_hub

    detail = get_clawhub_skill(slug)
    skill_md = detail.get("skill_md", "")
    if not skill_md:
        raise ValueError(f"No SKILL.md content for {slug!r}")

    # Safety scan before install
    is_safe, reasons = skill_hub.scan_skill(skill_md)
    if not is_safe:
        raise ValueError(
            f"Skill {slug!r} blocked by safety scanner: "
            f"{'; '.join(reasons[:3])}"
        )

    # Parse name and description from frontmatter if present
    name = detail.get("displayName") or slug
    description = detail.get("summary") or f"ClawHub skill: {slug}"

    # Try to extract frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---\n", skill_md, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        desc_m = re.search(r"^description:\s*[\"']?(.+?)[\"']?$", fm, re.MULTILINE)
        if name_m:
            name = name_m.group(1).strip().strip("\"'")
        if desc_m:
            description = desc_m.group(1).strip().strip("\"'")

    # Install via learning (SKILL.md format, same as ClawHub)
    # Strip frontmatter for the content body, keep it in references
    body = skill_md
    if fm_match:
        body = skill_md[fm_match.end():].strip()

    path = learning.save_learned_skill(
        name=f"clawhub-{slug}",
        description=f"[ClawHub] {description}",
        content=body,
    )
    return path


def trending_clawhub(limit: int = 10) -> list[dict[str, Any]]:
    """Get trending/popular ClawHub skills."""
    limit = max(1, min(limit, 50))  # [CH1] clamp like search_clawhub
    data = _api_get("/skills", {"limit": limit, "sort": "installs"})
    results = []
    for item in data.get("items", []):
        stats = item.get("stats", {})
        results.append({
            "slug": item.get("slug", ""),
            "displayName": item.get("displayName", ""),
            "summary": item.get("summary", ""),
            "installs": stats.get("installs", 0),
            "stars": stats.get("stars", 0),
        })
    results.sort(key=lambda x: x["installs"], reverse=True)
    return results[:limit]
