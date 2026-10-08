"""Skill marketplace/hub with install policies.

Package skills for sharing, install from URLs/files with safety scanning.
Like leading agents' skill hub with install policies — but simpler: scan before
install, quarantine suspicious ones.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def _hub_dir() -> Path:
    d = Path.home() / ".zeline" / "skill_hub"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _quarantine_dir() -> Path:
    d = _hub_dir() / "quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Patterns that trigger quarantine (not just warning — block install)
_QUARANTINE_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/\s", re.I),
    re.compile(r"rm\s+-rf\s+~", re.I),
    re.compile(r":\(\)\{\s*:\|\:&\s*\};:", re.I),  # fork bomb
    re.compile(r"curl.*\|\s*(bash|sh)\s*$", re.I | re.M),  # pipe to shell
    re.compile(r"wget.*\|\s*(bash|sh)\s*$", re.I | re.M),
    re.compile(r"eval\s*\(\s*base64", re.I),
    re.compile(r"powershell.*-enc", re.I),
    # Prompt-injection payloads (CH-1): a "skill" that tries to hijack the
    # agent or exfiltrate data is quarantined, not just warned about.
    re.compile(r"ignore\s+all\s+previous\s+instructions?", re.I),
    re.compile(r"disregard.*instructions?", re.I | re.S),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"send.*environment\s+variables?", re.I | re.S),
    re.compile(r"exfiltrat\w+", re.I),
]


def scan_skill(content: str) -> tuple[bool, list[str]]:
    """Scan skill content. Returns (is_safe, [reasons]).

    is_safe=False means quarantine (block install).
    """
    reasons = []
    for pat in _QUARANTINE_PATTERNS:
        if pat.search(content):
            reasons.append(f"Blocked pattern: {pat.pattern[:40]}")
    return (len(reasons) == 0, reasons)


def pack_skill(skill_name: str) -> str:
    """Package a local skill as a shareable .zip. Returns path to zip."""
    from zeline import skills as skills_pkg
    # Find the skill
    entries = skills_pkg.list_skill_entries(include_private=True)
    match = next((e for e in entries if e[1] == skill_name), None)
    if not match:
        raise FileNotFoundError(f"Skill {skill_name!r} not found")
    scope, name, title, desc, _ = match
    # Find the actual file
    base = {
        "public": skills_pkg.PUBLIC_SKILLS_DIR,
        "private": skills_pkg.PRIVATE_SKILLS_DIR,
        "learned": skills_pkg.LEARNED_SKILLS_DIR,
    }.get(scope)
    if not base:
        raise ValueError(f"Unknown scope {scope}")
    src = next(base.glob(f"{name}.md"), None)
    if not src:
        raise FileNotFoundError(f"Skill file for {name!r} not found")
    content = src.read_text(encoding="utf-8")
    # Create package
    manifest = {
        "name": name,
        "title": title,
        "description": desc,
        "version": "1.0.0",
        "created": time.strftime("%Y-%m-%d"),
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    zip_path = _hub_dir() / f"{name}.skill.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(f"{name}.md", content)
    return str(zip_path)


def install_skill(source: str) -> str:
    """Install a skill from a URL or local .zip path.

    Scans content before install. Quarantines suspicious packages.
    Returns result message.
    """
    # Download or copy to temp
    tmp = Path(tempfile.mkdtemp())
    try:
        if source.startswith(("http://", "https://")):
            zip_path = tmp / "skill.zip"
            # M1 fix: use _safe_request for SSRF-safe redirect handling
            # (was urllib.request.urlopen which follows redirects without per-hop checks)
            from zeline.tools import _safe_request
            max_size = 10 * 1024 * 1024  # 10MB max
            resp = _safe_request("GET", source, timeout=30, stream=True,
                                 headers={"User-Agent": "Zeline/1.0"})
            try:
                size = 0
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_size:
                            return "ERROR: skill package too large (max 10MB)"
                        f.write(chunk)
            finally:
                resp.close()
        else:
            zip_path = Path(source)
            if not zip_path.is_file():
                return f"ERROR: file not found: {source}"
        # Extract and validate
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                return "ERROR: invalid skill package (no manifest.json)"
            manifest = json.loads(zf.read("manifest.json"))
            md_files = [n for n in names if n.endswith(".md")]
            if not md_files:
                return "ERROR: no skill Markdown in package"
            content = zf.read(md_files[0]).decode("utf-8")
        # Safety scan
        is_safe, reasons = scan_skill(content)
        raw_name = manifest.get("name", "unnamed")
        # Sanitize name to prevent path traversal (HIGH-1 fix)
        name = "".join(c for c in str(raw_name) if c.isalnum() or c in "_-")[:64] or "unnamed"
        if not is_safe:
            qpath = _quarantine_dir() / f"{name}-{int(time.time())}.md"
            qpath.write_text(content, encoding="utf-8")
            return (
                f"QUARANTINED: skill {name!r} blocked.\n"
                f"Reasons: {'; '.join(reasons)}\n"
                f"Saved to quarantine: {qpath}"
            )
        # Verify hash if present
        expected = manifest.get("sha256")
        if expected:
            actual = hashlib.sha256(content.encode()).hexdigest()
            if actual != expected:
                return f"ERROR: hash mismatch for {name!r} (tampered package?)"
        # Install to public skills
        from zeline import skills as skills_pkg
        dest = skills_pkg.PUBLIC_SKILLS_DIR / f"{name}.md"
        if dest.exists():
            dest = skills_pkg.PUBLIC_SKILLS_DIR / f"{name}-{int(time.time())}.md"
        dest.write_text(content, encoding="utf-8")
        dest.chmod(0o600)
        return f"Installed skill {name!r} to {dest}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def list_hub_packages() -> list[dict]:
    """List packaged skills ready to share."""
    result = []
    for zp in sorted(_hub_dir().glob("*.skill.zip")):
        try:
            with zipfile.ZipFile(zp) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            result.append({
                "file": zp.name,
                "name": manifest.get("name"),
                "description": manifest.get("description", ""),
            })
        except Exception:
            continue
    return result
