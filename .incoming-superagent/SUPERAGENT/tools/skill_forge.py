"""
tools/skill_forge.py — Self-Extending Skills (gated)  (v4.0)

Pas agent nemu capability gap, dia DRAFT skill baru buat dirinya — lalu ajukan
sebagai PROPOSAL (bukan auto-apply). Operator yang review + masukin + re-lock.
Pakai gerbang reflection.py yang udah ada (frozen-path aware).

Ini "agent yang numbuhin kemampuannya sendiri" — tapi tetap di tangan operator,
biar gak ada skill liar yang masuk diam-diam (selaras sk55 + integrity lock).
"""
from __future__ import annotations

from typing import Callable, Optional

SKILL_SYS = (
    "You write SKILL.md-style markdown for a crypto agent skill library. "
    "Given a capability gap, output a concise skill doc: mission, trigger keywords, "
    "steps, and safety notes. Anything touching funds MUST go through the governor. "
    "Output ONLY the markdown.")


def draft_skill(gap: str, suggested_name: str,
                llm_fn: Optional[Callable[[list], str]] = None) -> str:
    """Bikin draft markdown skill dari deskripsi gap. llm_fn default: model_registry."""
    if llm_fn is None:
        from model_registry import ModelRegistry
        reg = ModelRegistry()
        llm_fn = lambda msgs: reg.call_with_cascade(msgs)[0]
    return llm_fn([{"role": "system", "content": SKILL_SYS},
                   {"role": "user", "content": f"Capability gap: {gap}\nSuggested name: {suggested_name}"}])


def forge_proposal(gap: str, suggested_name: str,
                   llm_fn: Optional[Callable[[list], str]] = None) -> str:
    """Draft skill → tulis sebagai proposal (gak nyentuh skill library langsung)."""
    from reflection import propose, Proposal
    draft = draft_skill(gap, suggested_name, llm_fn)
    target = f"skills/{suggested_name}.md"   # USULAN path — bukan ditulis ke sini
    p = propose(Proposal(
        title=f"new skill: {suggested_name}",
        rationale=f"Capability gap terdeteksi: {gap}. Agent draft skill kandidat.",
        target_path=target,
        plan=f"Skill draft (REVIEW dulu, jangan langsung pakai):\n\n{draft}"))
    return str(p)


if __name__ == "__main__":
    import tempfile, os
    os.environ["HERMES_PROPOSALS"] = tempfile.mkdtemp()
    import importlib, reflection
    importlib.reload(reflection)
    path = forge_proposal(
        gap="belum bisa baca harga oracle Chainlink on-chain",
        suggested_name="sk18",
        llm_fn=lambda m: "# sk18 — Chainlink Price Reader\n\nMission: baca harga dari Chainlink aggregator...\n(draft)")
    print("proposal ditulis:", path.split("/")[-1])
    print("isi proposal nge-flag review:", "REVIEW dulu" in open(path).read())
