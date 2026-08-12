"""
tools/skill_market.py — Skill Marketplace Integration (skills.sh)  (v4.1)

Cari & pasang skill pihak ketiga DENGAN gerbang integritas. Ini titik masuk
paling berisiko di seluruh sistem (riset OpenClaw: skill jahat = vektor serangan
nyata buat agent yang pegang private key). Jadi defaultnya PARANOID:

- Skill yang diunduh masuk QUARANTINE (skills/_quarantine/), TIDAK aktif.
- WAJIB audit (sk11) sebelum dipindah ke skills/.
- Setelah operator pindah & acc → operator regenerate SKILLS.lock manual.
- TIDAK PERNAH auto-install + auto-activate. Friksi = fitur (sama prinsip sk55).

Keyless utk browsing publik; install = unduh ke quarantine doang.

Pakai:
    from skill_market import search, fetch_to_quarantine, audit_checklist
    search("excalidraw")                         # list kandidat
    path = fetch_to_quarantine("author/skill")   # → skills/_quarantine/...  (BELUM aktif)
    print(audit_checklist(path))                  # checklist sebelum aktivasi
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

try:
    import httpx
except Exception:                                  # noqa: BLE001
    httpx = None

ROOT = Path(__file__).resolve().parent.parent
QUARANTINE = ROOT / "skills" / "_quarantine"
REGISTRY = os.environ.get("SKILLS_MARKET_URL", "https://skills.sh")  # override utk mirror/self-host


def _require_httpx():
    if httpx is None:
        raise RuntimeError("pip install httpx — dibutuhkan utk skill_market.")


def search(query: str, limit: int = 20) -> list:
    """Cari skill di marketplace. Return list metadata (BUKAN install)."""
    _require_httpx()
    try:
        r = httpx.get(f"{REGISTRY}/api/search", params={"q": query, "limit": limit}, timeout=20)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:                          # noqa: BLE001
        return [{"error": f"search gagal: {e}", "hint": "cek SKILLS_MARKET_URL / koneksi"}]


def fetch_to_quarantine(ref: str) -> str:
    """Unduh skill `ref` (author/skill) ke QUARANTINE. TIDAK mengaktifkan.

    Return path quarantine. Wajib audit (sk11) + operator pindah manual ke skills/.
    """
    _require_httpx()
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    safe = ref.replace("/", "__")
    dest = QUARANTINE / safe
    dest.mkdir(exist_ok=True)
    r = httpx.get(f"{REGISTRY}/api/skill/{ref}/manifest", timeout=20)
    r.raise_for_status()
    manifest = r.json()
    (dest / "_market_manifest.json").write_text(json.dumps(manifest, indent=2))
    for f in manifest.get("files", []):
        fr = httpx.get(f"{REGISTRY}/api/skill/{ref}/raw/{f['path']}", timeout=30)
        fr.raise_for_status()
        target = dest / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fr.content)
    return str(dest)


AUDIT_ITEMS = [
    "Baca SETIAP file — ada exec/eval/subprocess/os.system tersembunyi?",
    "Ada akses private key / mnemonic / env sensitif yang gak relevan fungsinya?",
    "Ada URL/exfil ke domain asing? (curl/httpx/requests ke host gak dikenal)",
    "Ada instruksi yang nyuruh agent matiin governor / integrity / rail?",
    "Two-stage dropper? (file polos yang nanti ngunduh+jalanin payload)",
    "Izin/scope sesuai fungsi yang diklaim? (skill diagram gak butuh akses wallet)",
    "Setelah lolos: pindah ke skills/, lalu `python tools/skill_integrity.py generate` (operator).",
]


def audit_checklist(quarantine_path: str) -> str:
    """Checklist audit sebelum mengaktifkan skill dari quarantine (lihat sk11)."""
    files = [str(p.relative_to(quarantine_path)) for p in Path(quarantine_path).rglob("*") if p.is_file()]
    head = f"🔒 AUDIT sebelum aktivasi: {quarantine_path}\n   files: {files}\n"
    return head + "\n".join(f"  [ ] {i+1}. {it}" for i, it in enumerate(AUDIT_ITEMS))


if __name__ == "__main__":
    print(f"registry: {REGISTRY}")
    print(f"quarantine: {QUARANTINE}")
    print(audit_checklist(str(QUARANTINE)))
