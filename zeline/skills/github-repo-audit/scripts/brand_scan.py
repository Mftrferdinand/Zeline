#!/usr/bin/env python3
"""Brand-leak scanner for public repos — verify no upstream/personal brand
terms remain. Runs in two modes:

  local  — scan a cloned working tree (fast, pre-push check)
  live   — re-fetch every file from GitHub master and scan (post-push proof)

The `live` mode is the one that matters for a final report: a clean local
tree can still leave stale content on the remote if a push failed or hit the
wrong branch. Expect a literal "0 matches" before telling the user it's done.

Usage:
  python brand_scan.py local  /path/to/repo [/path/to/repo2 ...]
  python brand_scan.py live   OWNER REPO [REPO2 ...]      # branch defaults to master

Edit TERMS / ALLOW for the specific brand policy of the task.
"""
import glob
import json
import os
import re
import sys
import urllib.request

# Terms that must NOT appear in public repos: upstream runtime/lab names, other
# competing agent brands, internal codenames, and host-specific paths. EDIT THIS
# list to match your own project — do NOT list your own public brand here, only
# the names you want scrubbed OUT. Examples left as placeholders:
TERMS = [
    "anthropic", "nous" + " research", "nous" + "research",
    "cursor", "upstream",
    # add your own: old codenames, competing runtimes, personal handles, etc.
]
# Substrings that are legitimate context (compatibility lists, etc.) — skipped.
ALLOW = ["chatgpt, etc", "claude, chatgpt"]

PAT = re.compile("|".join(TERMS), re.IGNORECASE)


def flag(rel, i, line):
    low = line.lower()
    if any(a in low for a in ALLOW):
        return 0
    print(f"  {rel}:{i}: {line.strip()}  <<< FLAG")
    return 1


def scan_local(roots):
    total = 0
    for root in roots:
        n = 0
        for path in sorted(glob.glob(os.path.join(root, "**", "*"), recursive=True)):
            if not os.path.isfile(path) or "/.git/" in path or path.endswith(".pyc"):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = os.path.relpath(path, root)
            for i, line in enumerate(lines, 1):
                if PAT.search(line):
                    n += flag(rel, i, line)
        print(f"{root}: {n} flags")
        total += n
    return total


def _gh(url):
    req = urllib.request.Request(url, headers={"User-Agent": "brand-scan"})
    return urllib.request.urlopen(req, timeout=30).read().decode()


def scan_live(owner, repos, branch="master"):
    total = 0
    for r in repos:
        tree = json.loads(_gh(
            f"https://api.github.com/repos/{owner}/{r}/git/trees/{branch}?recursive=1"
        ))
        blobs = [t["path"] for t in tree["tree"] if t["type"] == "blob"]
        n = 0
        for p in blobs:
            try:
                txt = _gh(f"https://raw.githubusercontent.com/{owner}/{r}/{branch}/{p}")
            except Exception as e:  # noqa: BLE001
                print(f"  {r}/{p} FETCH-ERR {e}")
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                if PAT.search(line):
                    n += flag(f"{r}/{p}", i, line)
        print(f"{r}: {len(blobs)} files, {n} flags")
        total += n
    return total


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    mode = argv[1]
    if mode == "local":
        total = scan_local(argv[2:])
    elif mode == "live":
        total = scan_live(argv[2], argv[3:] or [argv[2]])
    else:
        print(f"unknown mode: {mode}")
        return 2
    print(f"\nTOTAL: {total}", "=> ALL CLEAN" if total == 0 else "=> STILL DIRTY")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
