"""
tools/reflection.py — Self-Improvement Loop  (v4.0)

Otak "self-improving mode". Tiga kemampuan, dengan gerbang ketat:

1. LEARN     — scan memory/log terbaru, distill pola jadi 'lesson' (disimpan ke memory).
2. AUTO-FIX  — masalah operasional yang REVERSIBLE & non-dana → diberesin sendiri,
               TANPA nunggu diminta. Cuma action di SAFE_AUTO_ACTIONS yang boleh.
3. PROPOSE   — apa pun di luar itu (edit skill, ubah config, sentuh dana/rail) →
               ditulis jadi PROPOSAL buat operator review. TIDAK PERNAH auto-apply.

═══════════════════════ KENAPA ADA GERBANG ═══════════════════════
Agent yang bebas nulis-ulang dirinya bisa ngehapus rail-nya sendiri (governor,
integrity check) atau nguras dana. Jadi:

- FROZEN_PATHS  : file safety-critical yang TIDAK BISA disentuh loop ini. Usaha
                  nulis ke situ = exception. Perubahan cuma via proposal + operator
                  yang regenerate SKILLS.lock manual.
- SAFE_AUTO_ACTIONS : satu-satunya hal yang boleh dieksekusi tanpa konfirmasi.
                  Semuanya reversible & gak mindahin dana.
- Self-improve TIDAK PERNAH: sign tx, ubah spend cap, regenerate SKILLS.lock,
                  matiin governor, atau ubah dirinya sendiri (reflection.py).

Semua aksi otonom dicatat ke audit log (~/.hermes/reflection-audit.log).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent           # .../openclaw
PROPOSALS_DIR = Path(os.environ.get("HERMES_PROPOSALS", "~/.hermes/proposals")).expanduser()
AUDIT_LOG = Path(os.environ.get("HERMES_REFLECTION_AUDIT", "~/.hermes/reflection-audit.log")).expanduser()

# ── HARD GUARDS — jangan longgarin tanpa paham konsekuensinya ──
FROZEN_PATHS = {
    "SOUL.md", "AGENTS.md", "USER.md",
    "skills/hermes/references/governor.md", "skills/hermes/scripts/governor.py",
    "skills/hermes/scripts/mev.py", "skills/hermes/references/security.md",
    "tools/skill_integrity.py", "tools/reflection.py", "SKILLS.lock",
    "skills/sk55.md", "tools/watchdog.py", "tools/vault.py", "tools/model_registry.py",
    "tools/planner.py", "tools/swarm.py", "tools/automation.py", "tools/skill_forge.py",
    # v4.1 — tool yang nyentuh OS/firewall/desktop/marketplace = surface sensitif,
    # loop self-improve TIDAK BOLEH ngeditnya (cegah jadi vektor serangan).
    "tools/hids.py", "tools/desktop_control.py", "tools/skill_market.py",
}
# Satu-satunya aksi yang boleh dieksekusi TANPA konfirmasi operator.
# Semua reversible, gak nyentuh dana, gak ubah file skill.
SAFE_AUTO_ACTIONS = {
    "retry_with_fallback_rpc",   # ganti RPC yang timeout ke yang lain
    "rotate_rpc",                # rotasi pool RPC publik
    "switch_llm_provider",       # cascade ke provider berikutnya (R7)
    "restart_crashed_process",   # restart proses yang mati (psk2/systemd) — bukan deploy baru
    "clear_cache",               # bersihin cache lokal
    "requote",                   # ambil quote ulang setelah slippage berubah
    "backoff_and_retry",         # exponential backoff untuk 429/5xx
}


def _audit(event: str, detail: dict):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **detail})
    with AUDIT_LOG.open("a") as f:
        f.write(line + "\n")


def is_frozen(rel_path: str) -> bool:
    return rel_path.lstrip("./") in FROZEN_PATHS


def guard_write(rel_path: str):
    """Panggil sebelum APAPUN yang mau nulis file dari loop self-improve."""
    if is_frozen(rel_path):
        _audit("BLOCKED_FROZEN_WRITE", {"path": rel_path})
        raise PermissionError(
            f"'{rel_path}' frozen — self-improve gak boleh nyentuh rail/safety file. "
            f"Ubah hanya lewat proposal + operator regenerate SKILLS.lock.")


# ───────────────────────── 1. LEARN ─────────────────────────
def learn_from_recent(memory_engine, lookback: int = 30) -> list[str]:
    """Scan memory terbaru, distill pola berulang jadi 'lesson'. Heuristik lokal,
    tanpa API. Contoh: blocker yang sama muncul >=2x → jadiin lesson eksplisit."""
    recent = memory_engine.recent(lookback)
    blockers = [m for m in recent if m.kind == "blocker"]
    lessons = []
    seen: dict[str, int] = {}
    for b in blockers:
        key = " ".join(sorted(set(b.tags.split(",")))) or b.content[:40]
        seen[key] = seen.get(key, 0) + 1
    for key, n in seen.items():
        if n >= 2:
            lesson = f"Pola berulang ({n}x) pada [{key}] — bikin handler/fallback permanen."
            memory_engine.remember(lesson, kind="lesson", tags=key, weight=2.0)
            lessons.append(lesson)
            _audit("LESSON_LEARNED", {"key": key, "count": n})
    return lessons


# ───────────────────────── 2. AUTO-FIX ─────────────────────────
@dataclass
class FixOutcome:
    action: str
    executed: bool
    result: str = ""


def can_autofix(action: str) -> bool:
    return action in SAFE_AUTO_ACTIONS


def auto_fix(action: str, handlers: dict[str, Callable[[], str]],
             context: Optional[dict] = None) -> FixOutcome:
    """
    Eksekusi fix HANYA kalau ada di allowlist DAN operator udah daftarin handler-nya.
    `handlers` = registry env-specific yang operator wire (mis. {'restart_crashed_process': fn}).
    Apa pun di luar allowlist → tolak (jadi kandidat proposal).
    """
    if not can_autofix(action):
        _audit("AUTOFIX_REFUSED", {"action": action, "reason": "not in SAFE_AUTO_ACTIONS"})
        return FixOutcome(action, False, "bukan safe action → harus jadi proposal")
    fn = handlers.get(action)
    if fn is None:
        return FixOutcome(action, False, "handler belum di-wire operator")
    try:
        res = fn()
        _audit("AUTOFIX_EXECUTED", {"action": action, "context": context or {}})
        return FixOutcome(action, True, str(res))
    except Exception as e:
        _audit("AUTOFIX_ERROR", {"action": action, "error": repr(e)})
        return FixOutcome(action, False, f"error: {e!r}")


# ───────────────────────── 3. PROPOSE ─────────────────────────
@dataclass
class Proposal:
    title: str
    rationale: str
    target_path: str          # file yang diusulkan diubah/dibuat
    plan: str                 # deskripsi / diff / kode usulan
    requires_operator: bool = field(default=False)


def propose(p: Proposal) -> Path:
    """Tulis proposal ke antrian buat review. TIDAK pernah ngubah target_path.
    Kalau target frozen → otomatis ditandai requires_operator + warning ekstra."""
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    frozen = is_frozen(p.target_path)
    p.requires_operator = p.requires_operator or frozen
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_title = "".join(c if c.isalnum() else "-" for c in p.title)[:50]
    path = PROPOSALS_DIR / f"{stamp}-{safe_title}.md"
    body = [
        f"# PROPOSAL: {p.title}",
        f"- target: `{p.target_path}`",
        f"- requires_operator_approval: **{p.requires_operator}**"
        + ("  ⚠️ FROZEN/RAIL — review ekstra hati-hati" if frozen else ""),
        "", "## Rationale", p.rationale, "", "## Plan", p.plan, "",
        "## Cara apply (operator)",
        "1. Review diff/plan di atas.",
        "2. Kalau setuju, lakukan perubahan **manual** (bukan agent).",
        "3. Audit: `python tools/skill_integrity.py verify` → harusnya flag file ini.",
        "4. Re-lock: `python tools/skill_integrity.py generate` (ini langkah operator, "
        "BUKAN agent — biar integrity tetap bermakna).",
    ]
    path.write_text("\n".join(body) + "\n")
    _audit("PROPOSAL_WRITTEN", {"title": p.title, "target": p.target_path,
                                "frozen": frozen, "file": str(path)})
    return path


# ───────────────────────── orchestration ─────────────────────────
def daily_cycle(memory_engine, handlers: Optional[dict] = None) -> dict:
    """Dipanggil dari HEARTBEAT (boot/end-of-session). Aman dijalankan berkali-kali."""
    lessons = learn_from_recent(memory_engine)
    summary = {"lessons_learned": lessons, "proposals_dir": str(PROPOSALS_DIR),
               "pending_proposals": len(list(PROPOSALS_DIR.glob("*.md"))) if PROPOSALS_DIR.exists() else 0}
    _audit("DAILY_CYCLE", summary)
    return summary


if __name__ == "__main__":
    # demo: guard nolak nulis ke rail; proposal ke file biasa boleh
    print("can_autofix('rotate_rpc'):", can_autofix("rotate_rpc"))
    print("can_autofix('sign_tx'):", can_autofix("sign_tx"))
    try:
        guard_write("skills/hermes/scripts/governor.py")
    except PermissionError as e:
        print("guard works:", str(e)[:60], "...")
    import tempfile, os as _os
    _os.environ["HERMES_PROPOSALS"] = tempfile.mkdtemp()
    globals()["PROPOSALS_DIR"] = Path(_os.environ["HERMES_PROPOSALS"])
    pth = propose(Proposal("tambah fallback RPC Base", "ankr timeout berulang",
                           "skills/hermes/scripts/browser_engine.py",
                           "tambah endpoint llamarpc ke pool Base"))
    print("proposal written:", pth.name)
    pth2 = propose(Proposal("ubah cap governor", "mau naikin limit",
                            "skills/hermes/scripts/governor.py", "ubah default cap"))
    print("frozen proposal flagged requires_operator:", "requires_operator_approval: **True**" in pth2.read_text())
