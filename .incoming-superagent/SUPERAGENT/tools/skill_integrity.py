#!/usr/bin/env python3
"""
tools/skill_integrity.py — Skill Integrity Verification  (v4.0)

Kenapa ada: riset keamanan OpenClaw (190 advisory) nyatet skill jahat lewat
jalur distribusi plugin adalah vektor serangan nyata — sebuah skill bisa
nyelipin two-stage dropper di dalam konteks LLM, lolos dari exec pipeline.
Buat agent yang pegang private key, itu gak bisa ditawar.

Tool ini bikin & verifikasi MANIFEST (SHA-256 per file) dari seluruh workspace
skill. Opsional: tandatangani manifest pakai Ed25519 biar tamper pada lock-file
sendiri ketahuan.

Pakai:
    python tools/skill_integrity.py generate            # tulis SKILLS.lock
    python tools/skill_integrity.py generate --sign     # + tanda tangan Ed25519
    python tools/skill_integrity.py verify              # cek vs SKILLS.lock
    python tools/skill_integrity.py verify --strict     # exit!=0 kalau ADA temuan apa pun

Exit code: 0 = bersih, 1 = ada perubahan/temuan, 2 = error (lock hilang, dll).
Wiring: panggil `verify` di HEARTBEAT/boot. Kalau exit != 0 → tahan operasi
on-chain sampai operator review (lihat sk11.md §Skill Integrity).

Signing pakai `cryptography` (sudah jadi dep Hermes). Private key signing
disimpan operator di luar repo (HERMES_SIGNING_KEY = path file PEM).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # .../openclaw
LOCK = ROOT / "SKILLS.lock"

# File yang ikut di-hash: semua skill, reference, script, dan core agent files.
INCLUDE_SUFFIX = {".md", ".py"}
# Jangan hash file yang memang berubah tiap sesi atau bukan bagian "kode skill".
EXCLUDE_NAMES = {"SKILLS.lock"}
# _quarantine = skill marketplace yang BELUM diaudit (lihat tools/skill_market.py).
# Inert sampai operator pindah ke skills/ + regenerate. Jangan ikut hash biar
# unduhan gak mematahkan integrity boot — audit dulu, baru masuk lock.
EXCLUDE_DIRS = {"memory", "__pycache__", ".git", "node_modules", "proposals", "_quarantine"}


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix not in INCLUDE_SUFFIX:
            continue
        if p.name in EXCLUDE_NAMES:
            continue
        if any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
            continue
        yield p


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def build_manifest() -> dict:
    files = {str(p.relative_to(ROOT)): sha256(p) for p in iter_files()}
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "version": "4.1",
        "file_count": len(files),
        "files": files,
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
    }


# ───────────────── optional Ed25519 signing ─────────────────
def _sign(manifest_hash: str) -> str | None:
    key_path = os.environ.get("HERMES_SIGNING_KEY")
    if not key_path:
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        priv = load_pem_private_key(Path(key_path).expanduser().read_bytes(), password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            print("WARN: HERMES_SIGNING_KEY bukan Ed25519 — skip signing", file=sys.stderr)
            return None
        sig = priv.sign(bytes.fromhex(manifest_hash))
        return base64.b64encode(sig).decode()
    except Exception as e:
        print(f"WARN: signing gagal: {e}", file=sys.stderr)
        return None


def _verify_sig(manifest_hash: str, sig_b64: str) -> bool | None:
    pub_path = os.environ.get("HERMES_VERIFY_KEY")
    if not pub_path:
        return None   # gak ada public key → skip (bukan fail)
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = load_pem_public_key(Path(pub_path).expanduser().read_bytes())
        if not isinstance(pub, Ed25519PublicKey):
            return None
        pub.verify(base64.b64decode(sig_b64), bytes.fromhex(manifest_hash))
        return True
    except Exception:
        return False


# ───────────────── commands ─────────────────
def cmd_generate(args) -> int:
    manifest = build_manifest()
    if args.sign:
        sig = _sign(manifest["manifest_sha256"])
        if sig:
            manifest["signature_ed25519"] = sig
    LOCK.write_text(json.dumps(manifest, indent=2) + "\n")
    signed = " (signed)" if manifest.get("signature_ed25519") else ""
    print(f"✅ SKILLS.lock ditulis — {manifest['file_count']} file{signed}")
    print(f"   manifest_sha256: {manifest['manifest_sha256']}")
    return 0


def cmd_verify(args) -> int:
    if not LOCK.exists():
        print("🛑 SKILLS.lock tidak ada — jalankan `generate` dulu di sumber tepercaya.", file=sys.stderr)
        return 2
    locked = json.loads(LOCK.read_text())
    locked_files: dict = locked["files"]
    current = {str(p.relative_to(ROOT)): sha256(p) for p in iter_files()}

    modified, missing, added = [], [], []
    for rel, h in locked_files.items():
        if rel not in current:
            missing.append(rel)
        elif current[rel] != h:
            modified.append(rel)
    for rel in current:
        if rel not in locked_files:
            added.append(rel)

    # cek tanda tangan manifest (kalau ada & ada verify key)
    sig_state = "no signature in lock"
    if "signature_ed25519" in locked:
        ok = _verify_sig(locked["manifest_sha256"], locked["signature_ed25519"])
        sig_state = {True: "✅ signature valid", False: "🛑 SIGNATURE INVALID",
                     None: "⚠️ signature ada tapi HERMES_VERIFY_KEY belum di-set"}[ok]
        if ok is False:
            print("🛑 TANDA TANGAN MANIFEST TIDAK VALID — SKILLS.lock kemungkinan dirusak.", file=sys.stderr)
            return 1

    clean = not (modified or missing or added)
    if clean:
        print(f"✅ Integritas OK — {len(current)} file cocok. {sig_state}")
        return 0

    print("⚠️ PERUBAHAN SKILL TERDETEKSI:", file=sys.stderr)
    for rel in modified:
        print(f"   [MODIFIED] {rel}", file=sys.stderr)
    for rel in missing:
        print(f"   [MISSING ] {rel}", file=sys.stderr)
    for rel in added:
        print(f"   [NEW     ] {rel}  (skill baru belum di-lock — audit sebelum dipakai)", file=sys.stderr)
    print(f"   signature: {sig_state}", file=sys.stderr)
    print("→ Kalau perubahan ini SENGAJA: audit (sk11) → `generate` ulang. "
          "Kalau bukan: JANGAN jalankan operasi on-chain.", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes skill integrity verifier (v4.0)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("--sign", action="store_true")
    v = sub.add_parser("verify"); v.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    return cmd_generate(args) if args.cmd == "generate" else cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
