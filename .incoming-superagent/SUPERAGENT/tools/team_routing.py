#!/usr/bin/env python3
"""
tools/team_routing.py — Team Hierarchy Router (V7)
Level 0-3 routing: Sovereign → Commander → Operator → Observer
Task assignment, conflict detection, billing attribution.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

# ─── Team hierarchy ───
LEVEL_SOVEREIGN  = 0  # Full control, treasury, governance, kill-switch
LEVEL_COMMANDER  = 1  # Deploy, manage L2, view treasury (no spend)
LEVEL_OPERATOR   = 2  # Execute in assigned domains, view dashboards
LEVEL_OBSERVER   = 3  # Read-only, reports, status queries

LEVEL_NAMES = {0: "Sovereign", 1: "Commander", 2: "Operator", 3: "Observer"}

DOMAINS = [
    "crypto", "dev", "content", "automation", "security",
    "research", "marketing", "design", "infra", "treasury"
]

@dataclass
class TeamMember:
    id: str
    name: str
    level: int
    domains: list[str] = field(default_factory=list)
    honorific: str = ""
    wallet: str = ""
    rate_card: dict[str, float] = field(default_factory=dict)  # domain → hourly rate USD

@dataclass
class Task:
    id: str
    assigned_to: Optional[str] = None
    created_by: str = ""
    domain: str = ""
    status: str = "pending"  # pending | active | done | blocked
    created_at: str = ""
    completed_at: Optional[str] = None
    billing_project: str = ""
    cost_estimate: float = 0.0

class TeamRouter:
    """Route tasks, detect conflicts, manage team operations."""

    def __init__(self, config_path: Optional[Path] = None):
        self.members: dict[str, TeamMember] = {}
        self.tasks: dict[str, Task] = {}
        self.active_sessions: dict[str, str] = {}  # member_id → session_key
        self.config_path = config_path or Path.home() / ".agent" / "team.json"
        self._load()

    def _load(self):
        if self.config_path.exists():
            with open(self.config_path) as f:
                cfg = json.load(f)
            for m in cfg.get("members", []):
                member = TeamMember(**m)
                self.members[member.id] = member

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "members": [m.__dict__ for m in self.members.values()],
            "tasks": {k: t.__dict__ for k, t in self.tasks.items()},
        }
        with open(self.config_path, "w") as f:
            json.dump(cfg, f, indent=2)

    def add_member(self, member: TeamMember):
        self.members[member.id] = member
        self.save()

    def get_level(self, member_id: str) -> int:
        return self.members.get(member_id, TeamMember("", "", 3)).level

    def can_execute(self, member_id: str, domain: str) -> bool:
        """Check if member can execute in given domain."""
        member = self.members.get(member_id)
        if not member:
            return False
        if member.level <= LEVEL_COMMANDER:  # Sovereign/Commander → all domains
            return True
        if member.level == LEVEL_OPERATOR:
            return domain in member.domains or "all" in member.domains
        return False  # Observer → read-only

    def can_spend(self, member_id: str) -> bool:
        return self.get_level(member_id) <= LEVEL_SOVEREIGN

    def can_view_treasury(self, member_id: str) -> bool:
        return self.get_level(member_id) <= LEVEL_COMMANDER

    def assign_task(self, task: Task) -> bool:
        """Auto-assign task to best-fit available member."""
        # Find members in this domain sorted by level (lowest=highest priority)
        candidates = [
            (m.level, m.id) for m in self.members.values()
            if self.can_execute(m.id, task.domain)
        ]
        if not candidates:
            return False

        candidates.sort()
        # Assign to lowest-level available member
        task.assigned_to = candidates[0][1]
        task.status = "active"
        task.created_at = datetime.now(timezone.utc).isoformat()
        self.tasks[task.id] = task
        self.save()
        return True

    def detect_conflicts(self) -> list[dict]:
        """Detect conflicts between active tasks."""
        conflicts = []
        active = [t for t in self.tasks.values() if t.status == "active"]
        for i, t1 in enumerate(active):
            for t2 in active[i+1:]:
                # Same domain, different members → potential duplication
                if t1.domain == t2.domain and t1.assigned_to != t2.assigned_to:
                    conflicts.append({
                        "type": "duplicate_domain",
                        "task1": t1.id, "task2": t2.id,
                        "domain": t1.domain,
                    })
                # Same resource (file, DB, API key) → collision detection placeholder
        return conflicts

    def get_dashboard(self) -> dict:
        """Generate team dashboard."""
        now = datetime.now(timezone.utc).isoformat()
        return {
            "team_size": len(self.members),
            "active_tasks": sum(1 for t in self.tasks.values() if t.status == "active"),
            "completed_today": sum(
                1 for t in self.tasks.values()
                if t.status == "done" and t.completed_at and t.completed_at[:10] == now[:10]
            ),
            "members": {
                mid: {
                    "name": m.name,
                    "level": LEVEL_NAMES[m.level],
                    "domains": m.domains,
                    "active_task_count": sum(
                        1 for t in self.tasks.values()
                        if t.assigned_to == mid and t.status == "active"
                    ),
                }
                for mid, m in self.members.items()
            },
            "conflicts": self.detect_conflicts(),
            "billing": self._billing_summary(),
        }

    def _billing_summary(self) -> dict:
        """Per-project billing summary."""
        projects = {}
        for t in self.tasks.values():
            if t.billing_project:
                p = projects.setdefault(t.billing_project, {"cost": 0, "tasks": 0})
                p["cost"] += t.cost_estimate
                p["tasks"] += 1
        return projects

    def escalate(self, task_id: str, reason: str) -> bool:
        """Escalate task to Sovereign level."""
        task = self.tasks.get(task_id)
        if not task or task.status != "blocked":
            return False
        task.status = "escalated"
        task.assigned_to = None  # Goes to Sovereign queue
        self.save()
        return True


# ─── CLI ───
if __name__ == "__main__":
    import sys
    router = TeamRouter()

    if len(sys.argv) < 2:
        print(json.dumps(router.get_dashboard(), indent=2))
    elif sys.argv[1] == "add":
        m = TeamMember(
            id=sys.argv[2],
            name=sys.argv[3],
            level=int(sys.argv[4]),
            domains=sys.argv[5].split(",") if len(sys.argv) > 5 else [],
        )
        router.add_member(m)
        print(f"Member {m.name} added (Level {LEVEL_NAMES[m.level]})")
    elif sys.argv[1] == "assign":
        t = Task(
            id=sys.argv[2],
            created_by=sys.argv[3] if len(sys.argv) > 3 else "system",
            domain=sys.argv[4] if len(sys.argv) > 4 else "general",
        )
        ok = router.assign_task(t)
        print(f"Task {t.id} assigned to {t.assigned_to}" if ok else "No eligible member")
    elif sys.argv[1] == "conflicts":
        print(json.dumps(router.detect_conflicts(), indent=2))
    else:
        print(json.dumps(router.get_dashboard(), indent=2))
