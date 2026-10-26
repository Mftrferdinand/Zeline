"""Skill Provisioning utility.

Utility buat agent Zeline yang:
1. Detect capability gap (skill / custom tool yang gak ada).
2. Cari referensi implementasi di GitHub (public repo).
3. Generate scaffold tool + skill yang diadaptasi dari referensi.
4. Mode dry-run: tampilin rencana + diff sebelum inject (approval gate).
5. Inject beneran setelah operator approve.

Dipakai oleh skill `skill-provisioner` dan bisa dipanggil langsung sebagai
tool `custom_skill_provision`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from zeline import config
from zeline import skills as skills_mod

TOOLS_DIR = config.DATA_DIR / "tools"
SKILLS_ROOT = config.DATA_DIR / "skills"
TMP_DIR = config.DATA_DIR / "provisioning"

GITHUB_API = "https://api.github.com"


def _api_get(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET ke GitHub API; kembalikan JSON atau error string.

    Handle rate limit: unauthenticated = 60 req/jam/IP. Kalo kena limit,
    suruh operator set GITHUB_TOKEN di env (authenticated = 5.000/jam).
    """
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "zeline-provisioner"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    cmd = ["curl", "-sSL", "-A", headers["User-Agent"], "-H", headers["Accept"]]
    if token:
        cmd += ["-H", headers["Authorization"]]
    if params:
        for k, v in params.items():
            cmd += ["-G", "--data-urlencode", f"{k}={v}"]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        return f"ERROR: GitHub API timeout ({url})"
    if re.search(r"API rate limit exceeded", out):
        return (
            "ERROR: GitHub API rate limit exceeded (unauthenticated = 60 req/jam/IP). "
            "Set GITHUB_TOKEN di env (https://github.com/settings/tokens, scope 'repo'/'public_repo') "
            "buat naikin ke 5.000 req/jam, atau tunggu reset jam berikutnya."
        )
    try:
        return json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return f"ERROR: bad JSON from GitHub: {out[:300]}"


def _api_repo_meta(repo: str) -> dict[str, Any]:
    """Ambil default_branch + info repo."""
    owner, name = repo.split("/")
    return _api_get(f"{GITHUB_API}/repos/{owner}/{name}")


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def list_capabilities() -> dict[str, list[dict[str, str]]]:
    """List semua skills + custom tools yang terinstall."""
    out: dict[str, list[dict[str, str]]] = {"skills": [], "tools": []}
    for scope, name, title, description in skills_mod.list_skill_entries(include_private=True):
        out["skills"].append({"name": name, "title": title, "description": description, "scope": scope})
    if TOOLS_DIR.is_dir():
        for f in sorted(TOOLS_DIR.glob("*.py")):
            if f.stem.startswith("_"):
                continue
            out["tools"].append({"name": f"custom_{f.stem}", "file": str(f)})
    return out


def has_capability(name: str) -> dict[str, Any]:
    """Cek apakah skill atau tool bernama `name` sudah ada.

    Return {"found": True, "kind": "skill"|"tool", "path": ...} atau
    {"found": False, "similar": [...]}.
    """
    normalized = re.sub(r"[^a-z0-9_-]", "", name.lower())
    found = skills_mod._find_skill(normalized, include_private=True)
    if found is not None and not isinstance(found, str):
        return {"found": True, "kind": "skill", "path": str(found)}
    tool = TOOLS_DIR / f"{normalized}.py"
    if tool.is_file():
        return {"found": True, "kind": "tool", "path": str(tool)}
    # fuzzy by keyword
    similar: list[str] = []
    for _, n, t, d in skills_mod.list_skill_entries(include_private=True):
        if normalized.split("-")[-1] in n.lower() or normalized.split("-")[-1] in (t or "").lower():
            similar.append(n)
    for f in TOOLS_DIR.glob("*.py"):
        if normalized.split("-")[-1] in f.stem.lower():
            similar.append(f"custom_{f.stem}")
    return {"found": False, "similar": sorted(set(similar))[:8]}


def search_github(query: str, per_page: int = 5) -> list[dict[str, Any]]:
    """Cari public repo GitHub sebagai referensi."""
    data = _api_get(f"{GITHUB_API}/search/repositories", {"q": query, "sort": "stars", "per_page": per_page})
    if isinstance(data, str):
        return [{"error": data}]
    items = data.get("items", [])
    return [
        {
            "full_name": r["full_name"],
            "description": r.get("description") or "",
            "html_url": r["html_url"],
            "stargazers_count": r.get("stargazers_count", 0),
            "language": r.get("language"),
            "default_branch": r.get("default_branch", "master"),
        }
        for r in items
    ]


def fetch_repo_tree(repo: str, branch: str | None = None) -> list[str] | str:
    """Ambil daftar file di repo; auto-detect default branch kalo branch None."""
    owner, name = repo.split("/")
    if branch is None:
        meta = _api_repo_meta(repo)
        if isinstance(meta, str):
            return meta
        branch = meta.get("default_branch", "master")
    data = _api_get(f"{GITHUB_API}/repos/{owner}/{name}/git/trees/{branch}", {"recursive": "1"})
    if isinstance(data, str):
        return data
    if "tree" not in data:
        return f"ERROR: no tree for {repo}@{branch}"
    return sorted(t["path"] for t in data["tree"] if t["type"] == "blob")


def fetch_file(repo: str, path: str, branch: str | None = None) -> str:
    """Ambil isi file mentah dari repo (max 200KB). Auto-detect default branch."""
    owner, name = repo.split("/")
    if branch is None:
        meta = _api_repo_meta(repo)
        if isinstance(meta, str):
            return meta
        branch = meta.get("default_branch", "master")
    url = f"{GITHUB_API}/repos/{owner}/{name}/contents/{path}"
    data = _api_get(url, {"ref": branch})
    if isinstance(data, str):
        return data
    if not isinstance(data, dict) or "content" not in data:
        return f"ERROR: cannot read {path} from {repo}"
    import base64

    raw = base64.b64decode(data["content"])
    if len(raw) > 200_000:
        return f"ERROR: file too large ({len(raw)} bytes)"
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: decode failed: {exc}"


def _clone_or_cache(repo: str, branch: str = "master") -> Path:
    """Clone shallow repo ke cache ~/.zeline/provisioning/cache/<repo>."""
    cache = TMP_DIR / "cache" / repo.replace("/", "__")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", "--single-branch", "-b", branch, f"https://github.com/{repo}.git", str(cache)], timeout=300)
    return cache


def grep_repo(repo: str, pattern: str, branch: str = "master") -> list[dict[str, str]]:
    """Cari pattern (regex) di seluruh file repo. Return match + path."""
    try:
        root = _clone_or_cache(repo, branch)
    except Exception as exc:
        return [{"error": f"clone failed: {exc}"}]
    hits: list[dict[str, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if re.search(pattern, text, re.IGNORECASE):
            rel = str(path.relative_to(root))
            # potong jadi cuplikan ~25 baris di sekitar match pertama
            lines = text.splitlines()
            idx = next((i for i, l in enumerate(lines) if re.search(pattern, l, re.IGNORECASE)), 0)
            start = max(0, idx - 3)
            hits.append({"path": rel, "snippet": "\n".join(lines[start : start + 25])})
            if len(hits) >= 10:
                break
    return hits


def generate_tool_scaffold(name: str, description: str, args: list[dict[str, str]], reference: str | None = None) -> str:
    """Generate draft custom tool Python file."""
    safe_name = re.sub(r"[^a-z0-9_]", "_", name.lower())
    fn_name = safe_name

    def _render_arg(a: dict) -> str:
        typ = a.get("type", "str")
        if "default" not in a:
            return f"{a['name']}: {typ}"
        val = a["default"]
        # Kalo type-nya str tapi default belum di-quote, quote biar valid Python.
        if typ == "str" and not (val.startswith(("'", '"')) and val.endswith(("'", '"'))):
            val = f'"{val}"'
        return f"{a['name']}: {typ} = {val}"

    sig_args = ", ".join(_render_arg(a) for a in args)
    arg_docs = "\n".join(f"    {a['name']}: {a.get('desc', '')}" for a in args)
    refs = f"\n\n    Reference: {reference}" if reference else ""
    return f'''"""{description}.{refs}

    Generated by skill-provisioner (dry-run). Review before injecting to
    ~/.zeline/tools/{safe_name}.py
"""
from __future__ import annotations

import subprocess
from typing import Any


def {fn_name}({sig_args}) -> str:
    """{description}

{arg_docs}
    """
    # TODO: implementasi diadaptasi dari referensi
    try:
        result = subprocess.run(
            ["echo", "TODO: replace with real command"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout or result.stderr or "done"
    except Exception as exc:
        return f"ERROR: {{exc}}"
'''


def generate_skill_scaffold(name: str, description: str, reference: str | None = None) -> str:
    """Generate draft skill markdown dengan frontmatter."""
    refs = f"Referensi: {reference}\n" if reference else ""
    return f'''---
name: {name}
description: {description}
category: auto-provisioned
---

# {name}

{description}

{refs}
## Kapan dipakai
- User meminta sesuatu yang belum punya skill/tool-nya di Zeline.

## Langkah kerja
1. Cek capability yang ada dengan `custom_skill_provision.has_capability`.
2. Cari referensi di GitHub via `custom_skill_provision.search_github`.
3. Baca file kunci referensi (fetch_file / grep_repo).
4. Adaptasi jadi tool Python di ~/.zeline/tools/ ATAU skill di ~/.zeline/skills/private/.
5. **DRY-RUN dulu** — tampilin rencana + diff, jangan inject sebelum operator approve.
6. Setelah approve, inject dan verifikasi.

## Catatan
- Skill ini di-generate otomatis oleh skill-provisioner. Review manual wajib.
'''


def dry_run_provision(intent: str) -> str:
    """Orkestrasi full workflow dalam mode DRY-RUN (tanpa inject apa pun).

    Return laporan terstruktur: gap → referensi → rencana → scaffold preview.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    intent = intent.strip()
    lines: list[str] = []
    lines.append(f"# DRY-RUN — Provisioning untuk: {intent}\n")

    check = has_capability(intent)
    if check["found"]:
        return (
            f"TIDAK ADA GAP: `{intent}` sudah ada sebagai {check['kind']} di `{check['path']}`. "
            "Tidak perlu provision apa pun."
        )
    lines.append("## 1) Gap detection")
    lines.append(f"- Capability `{intent}` **belum ada**.")
    if check.get("similar"):
        lines.append(f"- Yang mirip sudah ada: {', '.join(check['similar'])}")
    lines.append("")

    lines.append("## 2) Referensi GitHub")
    query = intent.replace(" ", "+") + "+zeline+tool"
    repos = search_github(query)
    if not repos:
        repos = search_github(intent.replace(" ", "+"))
    # Filter: buang repo yang deskripsinya cuma polluted script (heuristik kasar).
    repos = [r for r in repos if not r.get("error")]
    if not repos:
        return "\n".join(lines) + "\n\n**GAK ADA referensi GitHub** untuk query ini (atau rate-limited)."
    for r in repos:
        lines.append(f"- **{r['full_name']}** ({r['stargazers_count']}⭐, {r['language'] or '?'}) — {r['html_url']}")
    lines.append("")

    # Baca isi beneran dari 1-2 repo teratas (repo metadata + file kunci),
    # biar adaptasi bukan cuma nebak dari deskripsi.
    lines.append("## 2b) Isi referensi (yang kebaca)")
    read_ok: list[dict[str, str]] = []
    for r in repos[:2]:
        tree = fetch_repo_tree(r["full_name"])
        if isinstance(tree, str):
            lines.append(f"- {r['full_name']}: {tree}")
            continue
        # cari entry point: main.py / *.py di root / README
        candidates = [p for p in tree if p.endswith((".py", ".md"))]
        key = next((p for p in candidates if p in ("main.py", "app.py", "cli.py", "README.md")), candidates[0] if candidates else None)
        if key:
            content = fetch_file(r["full_name"], key)
            if isinstance(content, str) and not content.startswith("ERROR"):
                read_ok.append({"repo": r["full_name"], "file": key, "lines": len(content.splitlines())})
                lines.append(f"- **{r['full_name']}/{key}** ({len(content.splitlines())} baris) — preview:")
                lines.append("```")
                lines.append("\n".join(content.splitlines()[:18]))
                lines.append("```")
            else:
                lines.append(f"- {r['full_name']}/{key}: {content}")
        else:
            lines.append(f"- {r['full_name']}: gak ada file .py/.md yang kebaca")
    if not read_ok:
        lines.append("- Gak ada isi file yang berhasil dibaca (rate limit / repo kosong). Rencana di bawah pake scaffold umum.")
    lines.append("")

    # Fallback: adaptasi dari skill/tool yang UDAH ADA di setup lo (hemat GitHub call).
    lines.append("## 2c) Basis adaptasi di setup lo")
    caps = list_capabilities()
    similar_tools = [t["name"] for t in caps["tools"] if intent.split()[-1].lower() in t["name"].lower()]
    similar_skills = [s["name"] for s in caps["skills"] if intent.split()[-1].lower() in s["name"].lower()]
    if similar_tools or similar_skills:
        lines.append("Yang udah ada dan bisa di-extend:")
        for t in similar_tools[:5]:
            lines.append(f"- tool: `{t}`")
        for s in similar_skills[:5]:
            lines.append(f"- skill: `{s}`")
    else:
        lines.append("- Gak ada skill/tool yang mirip di setup lo → generate scaffold baru dari referensi GitHub.")
    lines.append("")

    lines.append("## 3) Rencana adaptasi")
    lines.append(f"- Buat custom tool `custom_{intent.replace(' ', '_').lower()}` di `~/.zeline/tools/`")
    lines.append(f"- Buat skill `{intent.replace(' ', '_').lower()}` di `~/.zeline/skills/private/`")
    lines.append("")

    lines.append("## 4) Scaffold tool (preview)")
    lines.append("```python")
    lines.append(
        generate_tool_scaffold(
            intent,
            f"Auto-generated tool for: {intent}",
            [{"name": "query", "type": "str", "desc": "input untuk operasi ini"}],
            reference=repos[0].get("html_url") if repos else None,
        )
    )
    lines.append("```")
    lines.append("")
    lines.append("## 5) Next step")
    lines.append(
        "Kalo lo approve rencana di atas, panggil `custom_skill_provision.inject_tool` / "
        "`custom_skill_provision.inject_skill` — atau edit dulu scaffold-nya sesuai referensi yang lo pilih."
    )
    report = TMP_DIR / f"dry_run_{intent.replace(' ', '_').lower()}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)


def inject_tool(code: str, name: str | None = None) -> str:
    """Tulis custom tool ke ~/.zeline/tools/ dan reload registry.

    HANYA dipanggil setelah operator approve dry-run.
    """
    safe_name = re.sub(r"[^a-z0-9_]", "_", (name or "provisioned_tool").lower())
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOLS_DIR / f"{safe_name}.py"
    if path.exists():
        (TOOLS_DIR / f"{safe_name}.py.bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(code, encoding="utf-8")
    return f"Tool di-inject ke `{path}` (backup ada di `{path}.bak`)."


def inject_skill(name: str, content: str) -> str:
    """Buat skill baru via manage_skill (create).

    HANYA dipanggil setelah operator approve dry-run.
    """
    from zeline.skills import manage_skill

    safe_name = re.sub(r"[^a-z0-9_-]", "-", name.lower())
    result = manage_skill("create", name=safe_name, content=content)
    return f"Skill di-inject: `private/{safe_name}/SKILL.md` — {result}"


def cleanup_cache() -> str:
    """Bersihkan cache clone repo buat hemat storage."""
    cache = TMP_DIR / "cache"
    if not cache.exists():
        return "Cache kosong."
    count = sum(1 for _ in cache.rglob("*"))
    import shutil

    shutil.rmtree(cache)
    return f"Cache dibersihkan: {count} file dihapus."
