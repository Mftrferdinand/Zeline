"""Publikasi skill pemilik ke repo Zeline dengan approval + scan sensitif.

Alur aman (dipicu oleh callback publish di Telegram, owner-only):

    1. Muat isi skill (private/public).
    2. Scrub identitas pribadi (nama, email, chat-id, project) -> placeholder.
    3. Scan 3-lapis atas versi yang SUDAH discrub:
         L1  rahasia keras (API key, token, private key, .env)  -> BLOKIR
         L2  identitas pribadi tersisa (email/HP/path rumah)    -> BLOKIR
         L3  kebocoran infra (base_url+key, group_key, proxy)   -> BLOKIR
       Temuan APA PUN di salah satu lapis -> publish diblokir, tunjuk barisnya.
    4. Kalau bersih: tampilkan preview + laporan scrub ke owner + tombol
       Publish/Batal. TIDAK ada yang ke GitHub sebelum tombol ditekan.
    5. Setelah owner tekan Publish: tulis file ke ``zeline/skills/`` di repo,
       ``git add`` file itu saja, commit, push. Kembalikan hasil nyata (branch,
       commit, status push) — tidak pernah mengarang sukses.

Modul ini murni logika; gateway Telegram yang memanggilnya (lihat
``gateways/telegram.py``). Tidak menulis token/rahasia ke mana pun.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from zeline import config, skills

# --------------------------------------------------------------------------- #
# Scrub identitas pribadi (samakan dgn scripts/scrub_personal.py, spesifik dulu)
# --------------------------------------------------------------------------- #
_SCRUBS: list[tuple[str, str]] = [
    (r"(?i)\bmftrferdinand@gmail\.com\b", "user@example.com"),
    (r"(?i)\bmahesa f\.? ferdinand\b", "the user"),
    (r"(?i)MftrferdinandDocs", "UserDocs"),
    (r"(?i)@?kedaicloudcsbot", "@mystore_cs_bot"),
    (r"(?i)@?kedaicloudbot", "@mystore_bot"),
    (r"(?i)MyStore-backend", "store-backend"),
    (r"(?i)@?web3addicter_bot", "@community_bot"),
    (r"(?i)Web3addicterSite", "CommunitySite"),
    (r"(?i)\bkd-fresh\b", "store-frontend"),
    (r"(?i)\bdompetin\b", "walletapp"),
    (r"(?i)\baequitas\b", "SampleApp"),
    (r"(?i)\bmftrferdinand\b", "user"),
    (r"(?i)\bmahesa\b", "the user"),
    (r"(?i)\bkedaicloud\b", "MyStore"),
    (r"(?i)\bweb3\s?addicter\b", "the community"),
    (r"(?i)\btwenty3ph?\b", "SampleProject"),
    (r"(?i)\bzeline-guide\b", "docs-site"),
    (r"\b7387183839\b", "<OWNER_CHAT_ID>"),
    (r"(?i)(?<![A-Za-z])aes(?![A-Za-z])", "the user"),
    # Path rumah Termux operator -> placeholder generik.
    (r"/data/data/com\.termux/files/home", "~"),
]


def scrub(text: str) -> tuple[str, int, list[str]]:
    """Ganti identitas pribadi dengan placeholder. Return (teks, jumlah, contoh)."""
    total = 0
    samples: list[str] = []
    for pattern, replacement in _SCRUBS:
        new_text, count = re.subn(pattern, replacement, text)
        if count:
            total += count
            samples.append(f"{count}x  {pattern} -> {replacement}")
        text = new_text
    return text, total, samples


# --------------------------------------------------------------------------- #
# Scan sensitif 3-lapis. Temuan apa pun -> publish diblokir.
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    layer: int
    label: str
    line_no: int
    excerpt: str


# L1: rahasia keras — pola nilai token/kunci yang tidak boleh ada sama sekali.
_L1_SECRETS: list[tuple[str, str]] = [
    ("OpenAI-style key", r"\bsk-[A-Za-z0-9]{20,}\b"),
    ("9Router/rc key", r"\brc_[A-Za-z0-9]{12,}\b"),
    ("GitHub token", r"\bgh[porsu]_[A-Za-z0-9]{20,}\b"),
    ("GitHub fine-grained PAT", r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    ("Slack token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Bearer token", r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    ("Private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("Secret assignment", r"(?i)\b(api[_-]?key|apikey|secret|token|password|passwd|group_key)\b\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{12,}"),
    ("JWT", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
]

# L2: identitas pribadi yang MASIH tersisa setelah scrub (harusnya sudah bersih).
_L2_IDENTITY: list[tuple[str, str]] = [
    ("Email", r"(?i)\b[A-Za-z0-9._%+\-]+@(?!example\.com)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ("Phone (+62)", r"\+62\d{7,13}\b"),
    ("Owner chat id", r"\b7387183839\b"),
    ("Termux home path", r"/data/data/com\.termux/files/home"),
    ("Operator handle", r"(?i)\bmftrferdinand\b"),
]

# L3: kebocoran infra/provider — endpoint internal / header rahasia.
_L3_INFRA: list[tuple[str, str]] = [
    ("Local proxy endpoint", r"localhost:20128"),
    ("extra_headers group_key", r"(?i)group_key"),
    ("TokenHarbor secret ref", r"(?i)tokenharbor[^\n]{0,40}(key|token|secret)"),
    ("Provider url+key inline", r"https?://[^\s]+[?&](api[_-]?key|token|key)="),
]

# Baris yang jelas placeholder/dokumentasi aman -> jangan false-positive.
_SAFE_HINTS = re.compile(
    r"(?i)(your[_-]?(api[_-]?key|token)|<[^>]+>|placeholder|example|xxxx|\.\.\.|redacted|"
    r"pakai key dari operator|key disediakan operator|dari operator)"
)


def _scan_layer(text: str, layer: int, patterns: list[tuple[str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for label, pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            # Lewati bila baris jelas contoh/placeholder aman.
            if _SAFE_HINTS.search(line):
                continue
            excerpt = line.strip()
            if len(excerpt) > 120:
                excerpt = excerpt[:117] + "..."
            findings.append(Finding(layer, label, line_no, excerpt))
    return findings


def scan_sensitive(text: str) -> list[Finding]:
    """Jalankan 3 lapis scan. Return semua temuan (kosong = aman)."""
    findings: list[Finding] = []
    findings += _scan_layer(text, 1, _L1_SECRETS)
    findings += _scan_layer(text, 2, _L2_IDENTITY)
    findings += _scan_layer(text, 3, _L3_INFRA)
    return findings


# --------------------------------------------------------------------------- #
# Persiapan publish (tanpa menyentuh git). Dipakai untuk membangun bubble review.
# --------------------------------------------------------------------------- #
@dataclass
class PublishPlan:
    name: str
    ok: bool
    scrubbed: str = ""
    scrub_count: int = 0
    scrub_samples: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    error: str = ""


def prepare(name: str) -> PublishPlan:
    """Muat skill, scrub, scan. Tidak melakukan git apa pun."""
    content = skills.load_skill(name, include_private=True)
    if content.startswith("ERROR") or content.startswith("ERROR:"):
        return PublishPlan(name=name, ok=False, error=content)
    scrubbed, count, samples = scrub(content)
    findings = scan_sensitive(scrubbed)
    return PublishPlan(
        name=name,
        ok=not findings,
        scrubbed=scrubbed,
        scrub_count=count,
        scrub_samples=samples,
        findings=findings,
    )


# --------------------------------------------------------------------------- #
# Commit + push nyata. Dipanggil HANYA setelah owner menekan tombol Publish.
# --------------------------------------------------------------------------- #
def _repo_root() -> Path | None:
    """Cari root git repo yang memuat package ``zeline`` ini."""
    package_dir = Path(config.__file__).resolve().parent  # .../zeline/zeline
    root = package_dir.parent                              # .../zeline
    if (root / ".git").is_dir():
        return root
    return None


def _bundled_skills_dir() -> Path:
    """Folder skill bawaan di dalam package (ikut ter-commit ke repo)."""
    return Path(config.__file__).resolve().parent / "skills"


def _run_git(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode, out


def publish(name: str, scrubbed_content: str) -> str:
    """Tulis skill ke repo, commit file itu saja, push. Return laporan nyata."""
    root = _repo_root()
    if root is None:
        return (
            "GAGAL: repo git Zeline tidak ditemukan dari package ini "
            "(instalasi non-repo). Publish dibatalkan; tidak ada yang di-push."
        )

    try:
        safe_name = skills._safe_name(name)  # validasi nama sekali lagi
    except ValueError as exc:
        return f"GAGAL: nama skill tidak valid: {exc}"

    dest = _bundled_skills_dir() / f"{safe_name}.md"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(scrubbed_content, encoding="utf-8")
    except OSError as exc:
        return f"GAGAL menulis file skill: {exc.__class__.__name__}: {exc}"

    rel = dest.relative_to(root)

    # Branch aktif (dilaporkan apa adanya; tidak memaksa push ke main).
    code, branch = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip() or "?"

    code, out = _run_git(root, "add", str(rel))
    if code != 0:
        return f"GAGAL git add:\n{out}"

    # Kalau tidak ada perubahan ter-stage, skill sudah identik di repo.
    code, _ = _run_git(root, "diff", "--cached", "--quiet")
    if code == 0:
        return (
            f"Skill '{safe_name}' sudah identik dengan yang ada di repo "
            f"(branch {branch}). Tidak ada yang perlu di-commit/push."
        )

    code, out = _run_git(
        root, "commit", "-m", f"skills: publish {safe_name} (owner-approved)"
    )
    if code != 0:
        return f"GAGAL git commit:\n{out}"

    code, commit_sha = _run_git(root, "rev-parse", "--short", "HEAD")
    commit_sha = commit_sha.strip()

    code, push_out = _run_git(root, "push", "origin", f"HEAD:{branch}")
    if code != 0:
        return (
            f"Commit dibuat ({commit_sha}) tapi PUSH GAGAL:\n{push_out}\n\n"
            f"File sudah ter-commit lokal di branch {branch}; jalankan "
            f"`git push` manual dari ~/zeline bila perlu."
        )

    # Verifikasi jujur: pastikan tidak ada commit lokal yang belum ke-push.
    code, ahead = _run_git(root, "rev-list", "--count", "@{u}..HEAD")
    verified = ahead.strip() == "0"
    verify_line = (
        "Terverifikasi: branch lokal sinkron dengan remote."
        if verified
        else f"Peringatan: masih ada {ahead.strip()} commit lokal belum ter-push."
    )

    return (
        f"PUBLISHED: skill '{safe_name}' -> {rel}\n"
        f"Branch : {branch}\n"
        f"Commit : {commit_sha}\n"
        f"Push   : sukses ke origin/{branch}\n"
        f"{verify_line}"
    )
