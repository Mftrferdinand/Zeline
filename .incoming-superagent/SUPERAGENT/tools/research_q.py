"""
tools/research_q.py — Deep Research Dispatcher (AI-Q style)  (v4.1)

Kirim task riset ke server riset khusus → poll → report bersitasi. Kalau server
gak dikonfigurasi, fallback ke riset lokal via model_registry (R7 cascade) +
fetch operator. Setiap klaim penting WAJIB punya sumber; tanpa sumber → "unverified".

⚠️ PRIVASI: mode server MENGIRIM query ke endpoint eksternal. Jangan masukin
secret/PII/strategi rahasia. Topik sensitif → set local_only=True.

Pakai:
    from research_q import deep_research
    job = deep_research("dampak EIP-4844 ke biaya L2", depth="thorough")
    print(job.report); print(job.sources)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

try:
    import httpx
except Exception:                               # noqa: BLE001
    httpx = None

RESEARCH_URL = os.environ.get("RESEARCH_Q_URL")          # endpoint server riset (opsional)
RESEARCH_KEY = os.environ.get("RESEARCH_Q_KEY")


@dataclass
class ResearchJob:
    query: str
    report: str = ""
    sources: list = field(default_factory=list)
    status: str = "pending"
    mode: str = ""

    def summary(self) -> str:
        n = len(self.sources)
        unv = "" if n else "  ⚠️ tanpa sumber — tandai UNVERIFIED"
        return f"🔬 research [{self.mode}/{self.status}] {n} sumber{unv}\n{self.report[:400]}"


def _via_server(query: str, depth: str) -> ResearchJob:
    if httpx is None:
        raise RuntimeError("pip install httpx")
    headers = {"Authorization": f"Bearer {RESEARCH_KEY}"} if RESEARCH_KEY else {}
    r = httpx.post(f"{RESEARCH_URL}/research", json={"query": query, "depth": depth},
                   headers=headers, timeout=30)
    r.raise_for_status()
    jid = r.json()["job_id"]
    for _ in range(120):                        # poll s/d ~10 menit
        time.sleep(5)
        s = httpx.get(f"{RESEARCH_URL}/research/{jid}", headers=headers, timeout=30).json()
        if s.get("status") == "done":
            return ResearchJob(query, s.get("report", ""), s.get("sources", []), "done", "server")
        if s.get("status") == "error":
            raise RuntimeError(s.get("message", "server error"))
    raise TimeoutError("research job timeout")


def _via_local(query: str, depth: str) -> ResearchJob:
    """Fallback: pakai harness deep-research host kalau ada, atau model_registry.

    Di sini cuma kerangka — agent host (Claude/Cursor) yang punya web fetch yang
    sebenernya ngejalanin. Return job kosong + instruksi kalau gak ada provider.
    """
    try:
        from model_registry import ModelRegistry          # type: ignore
        reg = ModelRegistry()
        prompt = (f"Riset pertanyaan ini ({depth}). Untuk SETIAP klaim penting, kasih sumber. "
                  f"Format: laporan markdown + daftar sumber [n]. Tandai yang gak ada sumber sebagai UNVERIFIED.\n\n{query}")
        report = reg.call_with_cascade([{"role": "user", "content": prompt}])
        return ResearchJob(query, report, [], "done", "local-llm")
    except Exception as e:                                 # noqa: BLE001
        return ResearchJob(query, f"[no provider] {e} — gunakan harness deep-research host "
                                  f"atau `add model` (sk7), atau set RESEARCH_Q_URL.",
                           [], "error", "local")


def deep_research(query: str, depth: str = "standard", local_only: bool = False) -> ResearchJob:
    """Deep research. depth: quick|standard|thorough. local_only → jangan kirim ke server eksternal."""
    if RESEARCH_URL and not local_only:
        try:
            return _via_server(query, depth)
        except Exception as e:                            # noqa: BLE001
            job = _via_local(query, depth)
            job.report = f"[server gagal: {e} → fallback lokal]\n\n{job.report}"
            return job
    return _via_local(query, depth)


if __name__ == "__main__":
    job = deep_research("contoh pertanyaan riset", depth="quick", local_only=True)
    print(job.summary())
