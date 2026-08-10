#!/usr/bin/env python3
"""Konversi skill Hermes (folder SKILL.md + frontmatter) → format flat Zeline.

Zeline skill = 1 file .md:
    # <Title>
    > <description ringkas 1 baris>
    <body markdown>

Konverter ini:
  - Baca YAML frontmatter Hermes (name/description) → jadi judul + kutipan.
  - Inline isi references/*.md kecil (< limit) ke akhir body, karena Zeline
    tidak punya linked files.
  - Ganti/anotasi tool Hermes-only yang tidak ada di Zeline agar model tidak
    mencoba memanggilnya.
  - Tulis ke ~/.zeline/skills/public/<name>.md (tidak menimpa yang sudah ada
    kecuali --force).

Aman dijalankan berulang. Tidak menghapus apa pun di sisi Hermes.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

HERMES_SKILLS = os.path.expanduser("~/.hermes/skills")
ZELINE_PUBLIC = os.path.expanduser("~/.zeline/skills/public")

# Tool Hermes yang TIDAK ada di Zeline → diganti kalimat netral supaya model
# tidak menganggapnya tersedia.
TOOL_REWRITES = {
    r"\bdelegate_task\b": "kerjakan langsung (tanpa subagent)",
    r"\bskill_view\b": "load_skill",
    r"\bskill_manage\b": "save_skill/update_skill",
    r"\bsession_search\b": "cek memory/konteks",
    r"\bsend_message\b": "balas langsung ke user",
}
# Tool yang cukup dianotasi (masih relevan konsepnya) — ditandai sebagai N/A.
TOOL_UNSUPPORTED_NOTE = ["clarify", "cronjob", "vision_analyze", "text_to_speech", "process("]

# Scrub branding Hermes → Zeline. Skill yang di-import TIDAK boleh menyebut Hermes:
# ini repo/agent milik user (Zeline), bukan Hermes. Urutan penting (spesifik dulu).
BRAND_SCRUBS = [
    (r"Hermes Agent", "Zeline"),
    (r"HermesAgent", "ZelineAgent"),
    (r"HermesGuide", "ZelineGuide"),
    (r"hermes-agent", "zeline"),
    (r"hermes_tools", "zeline_tools"),
    (r"_hermes_env", "_zeline_env"),
    (r"~/\.hermes", "~/.zeline"),
    (r"\$HERMES_HOME", "$ZELINE_HOME"),
    (r"HERMES_", "ZELINE_"),
    (r"\bhermes\b", "zeline"),
    (r"\bHermes\b", "Zeline"),
    (r"\bHERMES\b", "ZELINE"),
]

REF_INLINE_LIMIT = 12_000  # char; reference lebih besar dari ini hanya diringkas judulnya

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _safe_name(name: str) -> str | None:
    n = name.strip().lower().replace(" ", "-")
    n = "".join(c for c in n if c.isalnum() or c in "-_")
    return n if _NAME_RE.fullmatch(n) else None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    fm: dict[str, str] = {}
    key = None
    for line in fm_raw.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            key = m.group(1).strip()
            fm[key] = m.group(2).strip().strip("\"'")
        elif key and line.strip():
            fm[key] += " " + line.strip()
    return fm, body


def _rewrite_tools(text: str) -> tuple[str, list[str]]:
    changed: list[str] = []
    for pat, repl in TOOL_REWRITES.items():
        if re.search(pat, text):
            text = re.sub(pat, repl, text)
            changed.append(pat.strip("\\b"))
    return text, changed


def _scrub_brand(text: str) -> str:
    """Hapus semua jejak nama Hermes → Zeline (case-aware)."""
    for pat, repl in BRAND_SCRUBS:
        text = re.sub(pat, repl, text)
    return text


def _inline_references(skill_dir: str, body: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    parts: list[str] = []
    for sub in ("references", "templates", "scripts"):
        d = os.path.join(skill_dir, sub)
        if not os.path.isdir(d):
            continue
        for fp in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
            if not os.path.isfile(fp):
                continue
            rel = os.path.relpath(fp, skill_dir)
            try:
                content = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if fp.endswith((".md", ".txt", ".yaml", ".yml", ".json", ".py", ".sh")) and len(content) <= REF_INLINE_LIMIT:
                lang = "" if fp.endswith((".md", ".txt")) else fp.rsplit(".", 1)[-1]
                fence = f"\n\n---\n\n## Lampiran: `{rel}`\n\n"
                if lang:
                    fence += f"```{lang}\n{content}\n```\n"
                else:
                    fence += content + "\n"
                parts.append(fence)
            else:
                notes.append(rel)
    if parts:
        body = body + "".join(parts)
    return body, notes


def convert_one(skill_md: str, force: bool, dry: bool) -> tuple[str, str]:
    text = open(skill_md, encoding="utf-8", errors="replace").read()
    skill_dir = os.path.dirname(skill_md)
    fm, body = _parse_frontmatter(text)
    raw_name = fm.get("name") or os.path.basename(skill_dir)
    name = _safe_name(raw_name)
    if not name:
        return raw_name, "SKIP (nama tidak valid)"
    desc = fm.get("description", "").replace("\n", " ").strip()
    if len(desc) > 300:
        desc = desc[:297] + "..."
    title = raw_name.replace("-", " ").replace("_", " ").title()

    body, tool_changes = _rewrite_tools(body)
    body, oversized = _inline_references(skill_dir, body)

    # Buang H1 pertama di body jika ada — kita menaruh judul sendiri di header,
    # supaya tidak muncul dua "# Title" beruntun.
    body = re.sub(r"^\s*#\s+.+\n+", "", body, count=1)

    # Scrub semua jejak branding Hermes → Zeline dari body, judul, dan deskripsi.
    body = _scrub_brand(body)
    desc = _scrub_brand(desc)
    title = _scrub_brand(title)

    header = f"# {title}\n\n> {desc}\n\n" if desc else f"# {title}\n\n"
    footer = ""
    present = [t for t in TOOL_UNSUPPORTED_NOTE if t in text]
    if present or tool_changes or oversized:
        footer = "\n\n---\n\n## Catatan adaptasi Zeline\n"
        if tool_changes:
            footer += f"- Tool luar diganti ke padanan Zeline: {', '.join(sorted(set(tool_changes)))}.\n"
        if present:
            footer += f"- Tool berikut TIDAK tersedia di Zeline, abaikan instruksinya: {', '.join(present)}.\n"
        if oversized:
            footer += f"- File pendukung tidak di-inline (terlalu besar/biner): {', '.join(oversized[:10])}.\n"

    out = header + body.rstrip() + footer + "\n"

    dest = os.path.join(ZELINE_PUBLIC, f"{name}.md")
    if os.path.exists(dest) and not force:
        return name, "SKIP (sudah ada)"
    if dry:
        return name, f"DRY ({len(out)} char)"
    os.makedirs(ZELINE_PUBLIC, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(out)
    os.chmod(dest, 0o600)
    return name, f"OK ({len(out)} char)"


def main() -> int:
    global REF_INLINE_LIMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="nama folder skill Hermes yang mau dikonversi")
    ap.add_argument("--all", action="store_true", help="konversi semua skill Hermes")
    ap.add_argument("--force", action="store_true", help="timpa file tujuan bila sudah ada")
    ap.add_argument("--dry", action="store_true", help="jangan tulis, cuma laporkan")
    ap.add_argument("--ref-limit", type=int, default=REF_INLINE_LIMIT, help="batas char untuk inline reference")
    args = ap.parse_args()

    REF_INLINE_LIMIT = args.ref_limit

    all_md = glob.glob(os.path.join(HERMES_SKILLS, "**", "SKILL.md"), recursive=True)
    by_name = {}
    for p in all_md:
        fm, _ = _parse_frontmatter(open(p, encoding="utf-8", errors="replace").read())
        nm = fm.get("name") or os.path.basename(os.path.dirname(p))
        by_name[nm] = p

    if args.all:
        targets = list(by_name.values())
    else:
        targets = []
        for want in args.names:
            hit = by_name.get(want)
            if not hit:
                # cari fuzzy
                cands = [p for n, p in by_name.items() if want in n]
                if len(cands) == 1:
                    hit = cands[0]
            if hit:
                targets.append(hit)
            else:
                print(f"  ?? tidak ketemu: {want}")

    for p in targets:
        name, status = convert_one(p, force=args.force, dry=args.dry)
        print(f"  {status:22} {name}")
    print(f"\nTotal diproses: {len(targets)}  ->  {ZELINE_PUBLIC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
