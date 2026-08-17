"""state.py — resumable JSON state for the coordinator."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict


@dataclass
class ChallengeState:
    id: int
    name: str
    category: str
    status: str = "pending"      # pending|running|solved|failed|awaiting_approval
    attempts: int = 0
    flags_found: list[str] = field(default_factory=list)   # candidate flags from workers
    submitted_flag: str = ""
    workers: dict = field(default_factory=dict)            # model -> last status


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.challenges: dict[int, ChallengeState] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            for cid, cs in raw.get("challenges", {}).items():
                self.challenges[int(cid)] = ChallengeState(**cs)

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {"challenges": {str(k): asdict(v) for k, v in self.challenges.items()}},
                    fh, indent=2,
                )
            os.replace(tmp, self.path)

    def get(self, cid: int) -> ChallengeState | None:
        return self.challenges.get(cid)

    def upsert(self, cs: ChallengeState):
        with self._lock:
            self.challenges[cs.id] = cs
        self.save()

    def is_done(self, cid: int) -> bool:
        cs = self.challenges.get(cid)
        return bool(cs and cs.status in ("solved", "awaiting_approval"))

    def add_candidate(self, cid: int, flag: str, model: str):
        cs = self.challenges[cid]
        cs.flags_found.append(flag)
        cs.workers[model] = "found_flag"
        self.save()

    def pending_approval(self) -> list[ChallengeState]:
        return [c for c in self.challenges.values() if c.status == "awaiting_approval"]
