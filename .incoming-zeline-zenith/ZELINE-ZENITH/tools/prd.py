"""
tools/prd.py — Product & Spec Workflows  (v4.1)

To-PRD dan To-Issues (sk26): percakapan/notes → dokumen PRD terstruktur → daftar
issue/task ter-breakdown. Deterministik (template + ekstraksi heuristik); untuk
sintesis "berat" dari obrolan mentah, isi field lewat LLM (model_registry) dan
serahin ke fungsi ini buat strukturnya.

Stdlib only. Pakai:
    from prd import to_prd, to_issues
    doc = to_prd(notes, title="Fitur Referral")
    print(doc.markdown)
    for iss in to_issues(doc): print(iss["title"])
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PRD:
    title: str
    problem: str = ""
    goals: list = field(default_factory=list)
    non_goals: list = field(default_factory=list)
    users: str = ""
    requirements: dict = field(default_factory=dict)   # {"P0":[...], "P1":[...], "P2":[...]}
    metrics: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)

    @property
    def markdown(self) -> str:
        def sec(title, items):
            if not items:
                return f"## {title}\n_TBD_\n"
            body = "\n".join(f"- {i}" for i in items)
            return f"## {title}\n{body}\n"
        reqs = ["## Requirements"]
        for tier in ("P0", "P1", "P2"):
            items = self.requirements.get(tier, [])
            if items:
                reqs.append(f"### {tier}")
                reqs += [f"- [ ] {r}" for r in items]
        out = [f"# PRD — {self.title}\n",
               f"## Problem\n{self.problem or '_TBD_'}\n",
               sec("Goals", self.goals),
               sec("Non-Goals", self.non_goals),
               f"## Users\n{self.users or '_TBD_'}\n",
               "\n".join(reqs) + "\n",
               sec("Success Metrics", self.metrics),
               sec("Risks", self.risks),
               sec("Open Questions", self.open_questions)]
        return "\n".join(out)


# ekstraksi heuristik ringan dari notes (best-effort; LLM bisa isi lebih baik)
def _grep(text: str, *keys) -> list:
    out = []
    for line in text.splitlines():
        low = line.lower().strip("-*•").strip()
        if any(k in low for k in keys) and len(low) > 3:
            out.append(line.strip("-*• ").strip())
    return out


def to_prd(text: str, title: str = "Untitled", **overrides) -> PRD:
    """Bangun PRD dari notes. Field bisa di-override eksplisit (hasil LLM lebih akurat)."""
    doc = PRD(title=title)
    doc.problem = overrides.get("problem") or (_grep(text, "masalah", "problem", "pain") or [""])[0]
    doc.goals = overrides.get("goals") or _grep(text, "goal", "tujuan", "ingin", "biar")
    doc.non_goals = overrides.get("non_goals") or _grep(text, "non-goal", "bukan", "gak termasuk", "out of scope")
    doc.users = overrides.get("users") or (_grep(text, "user", "pengguna", "audiens", "pelanggan") or [""])[0]
    reqs = overrides.get("requirements")
    if not reqs:
        found = _grep(text, "harus", "wajib", "perlu", "fitur", "must", "should")
        reqs = {"P0": found[:5], "P1": found[5:10], "P2": found[10:]}
    doc.requirements = reqs
    doc.metrics = overrides.get("metrics") or _grep(text, "metrik", "metric", "sukses", "kpi", "target")
    doc.risks = overrides.get("risks") or _grep(text, "risik", "risk", "bahaya", "kalau gagal")
    doc.open_questions = overrides.get("open_questions") or _grep(text, "?", "belum tau", "open question", "tbd")
    return doc


def to_issues(doc: PRD) -> list:
    """PRD → list issue siap-push (GitHub/Linear/Jira via sk6). Urut by prioritas."""
    issues = []
    order = {"P0": 0, "P1": 1, "P2": 2}
    for tier in ("P0", "P1", "P2"):
        for r in doc.requirements.get(tier, []):
            issues.append({
                "title": r if r[:1].isupper() else r.capitalize(),
                "body": f"Dari PRD: {doc.title}\n\nAcceptance: <definisikan kapan ini 'selesai'>\n\nPrioritas: {tier}",
                "labels": [tier.lower(), "from-prd"],
                "estimate": "S" if tier == "P0" else "M",
                "depends_on": [],
                "priority": order[tier],
            })
    return issues


def to_issues_markdown(doc: PRD) -> str:
    lines = [f"# Issues — {doc.title}\n"]
    for iss in to_issues(doc):
        lines.append(f"- [ ] **{iss['title']}** `{','.join(iss['labels'])}` (est {iss['estimate']})")
    return "\n".join(lines)


if __name__ == "__main__":
    notes = ("Masalah: user susah ngajak temen. Tujuan: naikin signup 20%. "
             "Harus ada link referral unik. Wajib track konversi. Fitur dashboard referral. "
             "Bukan termasuk: reward crypto. Risiko: fraud self-referral. Metrik sukses: signup via referral.")
    d = to_prd(notes, title="Referral")
    print(d.markdown)
    print("\n" + to_issues_markdown(d))
