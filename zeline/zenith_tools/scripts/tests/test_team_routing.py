#!/usr/bin/env python3
"""
tests/test_team_routing.py — Unit tests for TeamRouter class (V7)
Tests: add_member, get_level, can_execute, can_spend, can_view_treasury,
assign_task across levels, conflict detection, dashboard generation.
"""

import sys
import os
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

# Ensure the tools module is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.team_routing import (
    TeamRouter, TeamMember, Task,
    LEVEL_SOVEREIGN, LEVEL_COMMANDER, LEVEL_OPERATOR, LEVEL_OBSERVER,
    LEVEL_NAMES, DOMAINS,
)


class TestLevelConstants(unittest.TestCase):
    """Test that hierarchy constants are correctly defined."""

    def test_levels_ascending_restriction(self):
        """Higher number = lower privilege."""
        self.assertLess(LEVEL_SOVEREIGN, LEVEL_COMMANDER)
        self.assertLess(LEVEL_COMMANDER, LEVEL_OPERATOR)
        self.assertLess(LEVEL_OPERATOR, LEVEL_OBSERVER)

    def test_level_names_mapped(self):
        self.assertEqual(LEVEL_NAMES[LEVEL_SOVEREIGN], "Sovereign")
        self.assertEqual(LEVEL_NAMES[LEVEL_COMMANDER], "Commander")
        self.assertEqual(LEVEL_NAMES[LEVEL_OPERATOR], "Operator")
        self.assertEqual(LEVEL_NAMES[LEVEL_OBSERVER], "Observer")

    def test_domains_not_empty(self):
        self.assertGreater(len(DOMAINS), 0)
        self.assertIn("crypto", DOMAINS)
        self.assertIn("treasury", DOMAINS)


class TestTeamMemberDataclass(unittest.TestCase):
    """Test TeamMember dataclass."""

    def test_create_basic_member(self):
        m = TeamMember(
            id="m1",
            name="Alice",
            level=LEVEL_SOVEREIGN,
            domains=["crypto", "dev"],
        )
        self.assertEqual(m.id, "m1")
        self.assertEqual(m.name, "Alice")
        self.assertEqual(m.level, LEVEL_SOVEREIGN)
        self.assertEqual(m.domains, ["crypto", "dev"])
        self.assertEqual(m.honorific, "")
        self.assertEqual(m.wallet, "")
        self.assertEqual(m.rate_card, {})

    def test_create_full_member(self):
        m = TeamMember(
            id="m2",
            name="Bob",
            level=LEVEL_OPERATOR,
            domains=["content", "design"],
            honorific="Mas",
            wallet="0xBOB",
            rate_card={"content": 50.0, "design": 75.0},
        )
        self.assertEqual(m.honorific, "Mas")
        self.assertEqual(m.wallet, "0xBOB")
        self.assertAlmostEqual(m.rate_card["content"], 50.0)

    def test_empty_domains_allowed(self):
        m = TeamMember(id="m3", name="Observer", level=LEVEL_OBSERVER)
        self.assertEqual(m.domains, [])


class TestTaskDataclass(unittest.TestCase):
    """Test Task dataclass."""

    def test_create_basic_task(self):
        t = Task(
            id="task-1",
            created_by="alice",
            domain="crypto",
        )
        self.assertEqual(t.id, "task-1")
        self.assertEqual(t.status, "pending")
        self.assertIsNone(t.assigned_to)
        self.assertIsNone(t.completed_at)
        self.assertEqual(t.cost_estimate, 0.0)

    def test_task_statuses(self):
        statuses = ["pending", "active", "done", "blocked"]
        for status in statuses:
            t = Task(id=f"t-{status}", status=status,
                     created_by="system", domain="general")
            self.assertEqual(t.status, status)


class TestTeamRouterAddMember(unittest.TestCase):
    """Test add_member and member lookup."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_add_single_member(self):
        m = TeamMember(id="alice", name="Alice", level=LEVEL_SOVEREIGN,
                       domains=["crypto", "dev"])
        self.router.add_member(m)
        self.assertIn("alice", self.router.members)
        self.assertEqual(self.router.members["alice"].name, "Alice")

    def test_add_multiple_members(self):
        for i in range(5):
            m = TeamMember(id=f"user-{i}", name=f"User {i}",
                           level=LEVEL_OPERATOR, domains=["crypto"])
            self.router.add_member(m)
        self.assertEqual(len(self.router.members), 5)

    def test_add_member_persists(self):
        m = TeamMember(id="bob", name="Bob", level=LEVEL_COMMANDER,
                       domains=["infra", "security"])
        self.router.add_member(m)

        r2 = TeamRouter(config_path=self.config)
        self.assertIn("bob", r2.members)
        self.assertEqual(r2.members["bob"].level, LEVEL_COMMANDER)


class TestGetLevel(unittest.TestCase):
    """Test get_level for members and non-members."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)
        self.router.add_member(TeamMember(
            id="sovereign", name="S", level=LEVEL_SOVEREIGN, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="commander", name="C", level=LEVEL_COMMANDER, domains=["dev"],
        ))
        self.router.add_member(TeamMember(
            id="operator", name="O", level=LEVEL_OPERATOR, domains=["content"],
        ))
        self.router.add_member(TeamMember(
            id="observer", name="Ob", level=LEVEL_OBSERVER, domains=[],
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_get_level_known(self):
        self.assertEqual(self.router.get_level("sovereign"), LEVEL_SOVEREIGN)
        self.assertEqual(self.router.get_level("commander"), LEVEL_COMMANDER)
        self.assertEqual(self.router.get_level("operator"), LEVEL_OPERATOR)
        self.assertEqual(self.router.get_level("observer"), LEVEL_OBSERVER)

    def test_get_level_unknown_defaults_observer(self):
        self.assertEqual(self.router.get_level("nobody"), LEVEL_OBSERVER)


class TestCanExecute(unittest.TestCase):
    """Test can_execute across hierarchy levels."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

        self.router.add_member(TeamMember(
            id="sov", name="Sovereign", level=LEVEL_SOVEREIGN, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="com", name="Commander", level=LEVEL_COMMANDER, domains=["dev"],
        ))
        self.router.add_member(TeamMember(
            id="op-crypto", name="Crypto Op", level=LEVEL_OPERATOR,
            domains=["crypto", "dev"],
        ))
        self.router.add_member(TeamMember(
            id="op-content", name="Content Op", level=LEVEL_OPERATOR,
            domains=["content", "marketing"],
        ))
        self.router.add_member(TeamMember(
            id="op-all", name="All Op", level=LEVEL_OPERATOR,
            domains=["all"],
        ))
        self.router.add_member(TeamMember(
            id="obs", name="Observer", level=LEVEL_OBSERVER, domains=["crypto"],
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_sovereign_can_execute_any_domain(self):
        self.assertTrue(self.router.can_execute("sov", "crypto"))
        self.assertTrue(self.router.can_execute("sov", "unknown_domain"))
        self.assertTrue(self.router.can_execute("sov", "treasury"))

    def test_commander_can_execute_any_domain(self):
        self.assertTrue(self.router.can_execute("com", "dev"))
        self.assertTrue(self.router.can_execute("com", "crypto"))
        self.assertTrue(self.router.can_execute("com", "marketing"))

    def test_operator_can_execute_own_domain(self):
        self.assertTrue(self.router.can_execute("op-crypto", "crypto"))
        self.assertTrue(self.router.can_execute("op-crypto", "dev"))

    def test_operator_cannot_execute_other_domain(self):
        self.assertFalse(self.router.can_execute("op-crypto", "content"))
        self.assertFalse(self.router.can_execute("op-content", "crypto"))

    def test_operator_all_domain(self):
        for domain in DOMAINS:
            self.assertTrue(self.router.can_execute("op-all", domain))

    def test_observer_cannot_execute_any_domain(self):
        for domain in DOMAINS:
            self.assertFalse(self.router.can_execute("obs", domain))
        self.assertFalse(self.router.can_execute("obs", "crypto"))

    def test_unknown_member_cannot_execute(self):
        self.assertFalse(self.router.can_execute("phantom", "crypto"))


class TestCanSpend(unittest.TestCase):
    """Test can_spend — only Sovereign can spend."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)
        self.router.add_member(TeamMember(
            id="sov", name="S", level=LEVEL_SOVEREIGN, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="com", name="C", level=LEVEL_COMMANDER, domains=["dev"],
        ))
        self.router.add_member(TeamMember(
            id="op", name="O", level=LEVEL_OPERATOR, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="obs", name="Ob", level=LEVEL_OBSERVER,
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_sovereign_can_spend(self):
        self.assertTrue(self.router.can_spend("sov"))

    def test_commander_cannot_spend(self):
        self.assertFalse(self.router.can_spend("com"))

    def test_operator_cannot_spend(self):
        self.assertFalse(self.router.can_spend("op"))

    def test_observer_cannot_spend(self):
        self.assertFalse(self.router.can_spend("obs"))


class TestCanViewTreasury(unittest.TestCase):
    """Test can_view_treasury — Sovereign + Commander only."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)
        self.router.add_member(TeamMember(
            id="sov", name="S", level=LEVEL_SOVEREIGN, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="com", name="C", level=LEVEL_COMMANDER, domains=["dev"],
        ))
        self.router.add_member(TeamMember(
            id="op", name="O", level=LEVEL_OPERATOR, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="obs", name="Ob", level=LEVEL_OBSERVER,
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_sovereign_can_view(self):
        self.assertTrue(self.router.can_view_treasury("sov"))

    def test_commander_can_view(self):
        self.assertTrue(self.router.can_view_treasury("com"))

    def test_operator_cannot_view(self):
        self.assertFalse(self.router.can_view_treasury("op"))

    def test_observer_cannot_view(self):
        self.assertFalse(self.router.can_view_treasury("obs"))


class TestAssignTask(unittest.TestCase):
    """Test task assignment across levels and domains."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

        # Hierarchy: Sovereign > Commander > Operator
        self.router.add_member(TeamMember(
            id="sov", name="Sovereign", level=LEVEL_SOVEREIGN, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="com", name="Commander", level=LEVEL_COMMANDER, domains=["dev"],
        ))
        self.router.add_member(TeamMember(
            id="op", name="Operator", level=LEVEL_OPERATOR, domains=["crypto"],
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_assign_to_highest_priority(self):
        """Task should go to the lowest level (highest authority)."""
        task = Task(id="t1", created_by="system", domain="crypto")
        ok = self.router.assign_task(task)
        self.assertTrue(ok)
        # Should go to sov (level 0), not op (level 2)
        self.assertEqual(task.assigned_to, "sov")
        self.assertEqual(task.status, "active")
        self.assertIsNotNone(task.created_at)

    def test_assign_to_only_eligible(self):
        """Domain-only member gets the task when higher levels can't."""
        task = Task(id="t2", created_by="system", domain="content")
        # Neither sov nor com have "content" explicitly,
        # but sov/commander have LEVEL <= COMMANDER so they can execute any domain
        ok = self.router.assign_task(task)
        self.assertTrue(ok)
        self.assertEqual(task.assigned_to, "sov")

    def test_assign_no_eligible_member(self):
        task = Task(id="t3", created_by="system", domain="crypto")
        # All members removed from router → no one eligible
        empty_router = TeamRouter(config_path=Path(tempfile.mkdtemp()) / "team.json")
        ok = empty_router.assign_task(task)
        self.assertFalse(ok)
        self.assertIsNone(task.assigned_to)

    def test_assign_task_persists_in_router(self):
        task = Task(id="t_persist", created_by="system", domain="crypto")
        self.router.assign_task(task)

        r2 = TeamRouter(config_path=self.config)
        # Note: the _load method doesn't reload tasks currently, only members.
        # We test the in-memory state.
        self.assertIn("t_persist", self.router.tasks)

    def test_assign_multiple_tasks(self):
        tasks = [Task(id=f"task-{i}", created_by="system", domain="crypto")
                 for i in range(3)]
        for t in tasks:
            ok = self.router.assign_task(t)
            self.assertTrue(ok)
        self.assertEqual(len(self.router.tasks), 3)
        # All go to sovereign as highest priority
        for t in tasks:
            self.assertEqual(t.assigned_to, "sov")


class TestConflictDetection(unittest.TestCase):
    """Test conflict detection between active tasks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

        self.router.add_member(TeamMember(
            id="m1", name="Alice", level=LEVEL_OPERATOR, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="m2", name="Bob", level=LEVEL_OPERATOR, domains=["crypto", "dev"],
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_no_conflicts_single_task(self):
        task = Task(id="t1", created_by="system", domain="crypto")
        self.router.tasks["t1"] = task
        task.status = "active"
        task.assigned_to = "m1"

        conflicts = self.router.detect_conflicts()
        self.assertEqual(conflicts, [])

    def test_no_conflicts_different_domains(self):
        t1 = Task(id="t1", assigned_to="m1", domain="crypto", status="active")
        t2 = Task(id="t2", assigned_to="m2", domain="dev", status="active")
        self.router.tasks["t1"] = t1
        self.router.tasks["t2"] = t2

        conflicts = self.router.detect_conflicts()
        self.assertEqual(conflicts, [])

    def test_conflict_same_domain_different_assignees(self):
        t1 = Task(id="t1", assigned_to="m1", domain="crypto", status="active")
        t2 = Task(id="t2", assigned_to="m2", domain="crypto", status="active")
        self.router.tasks["t1"] = t1
        self.router.tasks["t2"] = t2

        conflicts = self.router.detect_conflicts()
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c["type"], "duplicate_domain")
        self.assertEqual(c["domain"], "crypto")
        self.assertIn(c["task1"], ["t1", "t2"])
        self.assertIn(c["task2"], ["t1", "t2"])

    def test_no_conflict_same_assignee(self):
        """Same domain + same assignee = no duplication conflict."""
        t1 = Task(id="t1", assigned_to="m1", domain="crypto", status="active")
        t2 = Task(id="t2", assigned_to="m1", domain="crypto", status="active")
        self.router.tasks["t1"] = t1
        self.router.tasks["t2"] = t2

        conflicts = self.router.detect_conflicts()
        self.assertEqual(conflicts, [])

    def test_only_active_tasks_considered(self):
        t1 = Task(id="t1", assigned_to="m1", domain="crypto", status="active")
        t2 = Task(id="t2", assigned_to="m2", domain="crypto", status="done")
        self.router.tasks["t1"] = t1
        self.router.tasks["t2"] = t2

        conflicts = self.router.detect_conflicts()
        self.assertEqual(conflicts, [])


class TestDashboard(unittest.TestCase):
    """Test dashboard generation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

        self.router.add_member(TeamMember(
            id="sov", name="Sovereign", level=LEVEL_SOVEREIGN, domains=["crypto"],
        ))
        self.router.add_member(TeamMember(
            id="op", name="Operator", level=LEVEL_OPERATOR,
            domains=["crypto", "content"],
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_dashboard_team_size(self):
        dash = self.router.get_dashboard()
        self.assertEqual(dash["team_size"], 2)

    def test_dashboard_active_tasks_zero(self):
        dash = self.router.get_dashboard()
        self.assertEqual(dash["active_tasks"], 0)

    def test_dashboard_with_active_tasks(self):
        t1 = Task(id="t1", assigned_to="op", domain="crypto", status="active")
        t2 = Task(id="t2", assigned_to="sov", domain="dev", status="active")
        t3 = Task(id="t3", assigned_to="op", domain="content", status="done")
        self.router.tasks["t1"] = t1
        self.router.tasks["t2"] = t2
        self.router.tasks["t3"] = t3

        dash = self.router.get_dashboard()
        self.assertEqual(dash["active_tasks"], 2)

    def test_dashboard_member_info(self):
        dash = self.router.get_dashboard()
        self.assertIn("sov", dash["members"])
        self.assertIn("op", dash["members"])
        self.assertEqual(dash["members"]["sov"]["name"], "Sovereign")
        self.assertEqual(dash["members"]["sov"]["level"], "Sovereign")
        self.assertEqual(dash["members"]["sov"]["domains"], ["crypto"])
        self.assertEqual(dash["members"]["op"]["level"], "Operator")
        self.assertEqual(dash["members"]["op"]["domains"], ["crypto", "content"])

    def test_dashboard_active_task_counts(self):
        t1 = Task(id="t1", assigned_to="op", domain="crypto", status="active")
        t2 = Task(id="t2", assigned_to="op", domain="content", status="active")
        self.router.tasks["t1"] = t1
        self.router.tasks["t2"] = t2

        dash = self.router.get_dashboard()
        self.assertEqual(dash["members"]["op"]["active_task_count"], 2)
        self.assertEqual(dash["members"]["sov"]["active_task_count"], 0)

    def test_dashboard_completed_today(self):
        now = datetime.now(timezone.utc).isoformat()
        t = Task(
            id="done-today", assigned_to="op", domain="crypto",
            status="done", completed_at=now,
        )
        self.router.tasks["done-today"] = t

        dash = self.router.get_dashboard()
        self.assertEqual(dash["completed_today"], 1)

    def test_dashboard_includes_conflicts(self):
        dash = self.router.get_dashboard()
        self.assertIn("conflicts", dash)
        self.assertIsInstance(dash["conflicts"], list)

    def test_dashboard_includes_billing(self):
        t = Task(
            id="billed", assigned_to="op", domain="crypto",
            billing_project="project-alpha", cost_estimate=150.0,
            status="active",
        )
        self.router.tasks["billed"] = t

        dash = self.router.get_dashboard()
        self.assertEqual(dash["billing"]["project-alpha"]["cost"], 150.0)
        self.assertEqual(dash["billing"]["project-alpha"]["tasks"], 1)

    def test_dashboard_empty_billing(self):
        dash = self.router.get_dashboard()
        self.assertEqual(dash["billing"], {})


class TestEscalate(unittest.TestCase):
    """Test task escalation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

        self.router.add_member(TeamMember(
            id="op", name="Operator", level=LEVEL_OPERATOR, domains=["crypto"],
        ))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_escalate_blocked_task(self):
        task = Task(
            id="t-blocked", assigned_to="op", domain="crypto",
            status="blocked",
        )
        self.router.tasks["t-blocked"] = task

        ok = self.router.escalate("t-blocked", "Need help")
        self.assertTrue(ok)
        self.assertEqual(task.status, "escalated")
        self.assertIsNone(task.assigned_to)

    def test_escalate_unknown_task(self):
        ok = self.router.escalate("does-not-exist", "reason")
        self.assertFalse(ok)

    def test_escalate_non_blocked_task(self):
        task = Task(id="t-active", assigned_to="op", domain="crypto",
                    status="active")
        self.router.tasks["t-active"] = task

        ok = self.router.escalate("t-active", "reason")
        self.assertFalse(ok)
        self.assertNotEqual(task.status, "escalated")


class TestTeamRouterEmpty(unittest.TestCase):
    """Test behavior with no members."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = Path(self.tmpdir) / "team.json"
        self.router = TeamRouter(config_path=self.config)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_dashboard(self):
        dash = self.router.get_dashboard()
        self.assertEqual(dash["team_size"], 0)
        self.assertEqual(dash["active_tasks"], 0)
        self.assertEqual(dash["members"], {})
        self.assertEqual(dash["conflicts"], [])
        self.assertEqual(dash["billing"], {})

    def test_empty_no_eligible_assignee(self):
        task = Task(id="t-empty", created_by="system", domain="crypto")
        ok = self.router.assign_task(task)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
