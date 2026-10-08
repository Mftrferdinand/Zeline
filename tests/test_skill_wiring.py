"""Tests wiring self-improving skills: telemetri, review, proposal, tools.

Fokus pada INTEGRASI antar modul (bukan logika tiap modul — itu sudah
di-cover di test_skill_telemetry/review/proposals): load_skill mencatat
telemetri, supervisor mengatribusikan outcome ke skill, tool review/proposal
terdaftar dengan risk class yang benar, dan approval selalu ditanya untuk
aksi yang mengubah prosedur agen.

Isolasi: pola nuclear ala test_supervisor.py — evict modul zeline dari
sys.modules + set ZELINE_HOME + import fresh di tiap test, supaya konstanta
level-modul (SKILLS_ROOT, MIGRATION_MARKER, DATA_DIR) selalu mengarah ke
tmp hermetic milik test ini.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _fresh_zeline(home: Path) -> dict:
    """Evict + set ZELINE_HOME + import fresh. Kembalikan modul-modul."""
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    import zeline.config
    import zeline.skills
    import zeline.skill_telemetry
    import zeline.skill_review
    import zeline.skill_proposals
    import zeline.supervisor
    import zeline.tools

    return {
        "skills": zeline.skills,
        "telemetry": zeline.skill_telemetry,
        "review": zeline.skill_review,
        "proposals": zeline.skill_proposals,
        "supervisor": zeline.supervisor,
        "tools": zeline.tools,
    }


class SkillWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self._saved_home = os.environ.get("ZELINE_HOME")
        self.mods = _fresh_zeline(self.home)
        skills = self.mods["skills"]
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        skills.PUBLIC_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved_home
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def _make_skill(self, name: str, description: str = "Skill uji.",
                    public: bool = False) -> Path:
        skills = self.mods["skills"]
        scope = skills.PUBLIC_SKILLS_DIR if public else skills.PRIVATE_SKILLS_DIR
        target = scope / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\nIsi {name}.\n",
            encoding="utf-8",
        )
        return target

    # -- load_skill -> telemetri -------------------------------------------

    def test_load_skill_records_telemetry(self):
        telemetry = self.mods["telemetry"]
        tools = self.mods["tools"]
        self._make_skill("catatan", public=True)
        executor = tools.ToolExecutor(identity="alice", profile="full")
        content = executor._handlers["load_skill"]("catatan")
        self.assertIn("Isi catatan", content)
        self.assertEqual(telemetry.stats("catatan", "alice")["loads"], 1)

    def test_broken_telemetry_does_not_break_load_skill(self):
        """Telemetri rusak = load tetap jalan (fail-safe)."""
        tools = self.mods["tools"]
        (self.home / "skill-telemetry").write_text("bukan direktori")
        self._make_skill("catatan", public=True)
        executor = tools.ToolExecutor(identity="alice", profile="full")
        content = executor._handlers["load_skill"]("catatan")
        self.assertIn("Isi catatan", content)

    # -- supervisor -> outcome ----------------------------------------------

    def _spawn_and_wait(self, supervisor, task, timeout=20, **spawn_kw):
        supervisor.spawn(task, **spawn_kw)
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = supervisor.poll_events()
            if events:
                return events[0]
            time.sleep(0.05)
        self.fail("worker tidak selesai tepat waktu")

    def test_supervisor_success_attributed_to_used_skill(self):
        telemetry = self.mods["telemetry"]
        Supervisor = self.mods["supervisor"].Supervisor

        def runner(task, grants, wid):
            telemetry.note_used("catatan")
            return "hasil kerja selesai"

        supervisor = Supervisor(identity="alice", task_runner=runner)
        supervisor.bind(profile="full", workspace=".", depth=0)
        try:
            event = self._spawn_and_wait(supervisor, "kerjakan sesuatu")
            self.assertEqual(event.status, "done")
            record = telemetry.stats("catatan", "alice")
            self.assertEqual(record["successes"], 1)
            self.assertEqual(record["consecutive_failures"], 0)
            # Tercatat di file milik owner (satu file, bukan per worker).
            import hashlib

            owner_file = (
                self.home / "skill-telemetry"
                / f"{hashlib.sha256(b'alice').hexdigest()[:32]}.json"
            )
            self.assertTrue(owner_file.is_file())
            self.assertEqual(
                len(list((self.home / "skill-telemetry").glob("*.json"))), 1
            )
        finally:
            supervisor.shutdown(timeout=5)

    def test_supervisor_failure_attributed_to_used_skill(self):
        telemetry = self.mods["telemetry"]
        Supervisor = self.mods["supervisor"].Supervisor

        def runner(task, grants, wid):
            telemetry.note_used("catatan")
            return ""  # hasil kosong -> verifikasi gagal

        supervisor = Supervisor(identity="alice", task_runner=runner)
        supervisor.bind(profile="full", workspace=".", depth=0)
        try:
            event = self._spawn_and_wait(
                supervisor, "kerjakan sesuatu", accept_if="frasa wajib"
            )
            self.assertEqual(event.status, "failed")
            record = telemetry.stats("catatan", "alice")
            self.assertEqual(record["failures"], 1)
            self.assertGreaterEqual(record["consecutive_failures"], 1)
        finally:
            supervisor.shutdown(timeout=5)

    def test_worker_identity_rolls_up_to_owner(self):
        """Load dari worker alice::wkrXXX tercatat di alice."""
        telemetry = self.mods["telemetry"]
        tools = self.mods["tools"]
        self._make_skill("catatan", public=True)
        executor = tools.ToolExecutor(identity="alice::wkr3f8a2b1c", profile="full")
        executor._handlers["load_skill"]("catatan")
        self.assertEqual(telemetry.stats("catatan", "alice")["loads"], 1)

    # -- tool review_skills / apply_skill_review -------------------------------

    def test_review_skills_tool_is_dry_run(self):
        tools = self.mods["tools"]
        self._make_skill("lama")
        out = tools._review_skills("alice")
        self.assertIsInstance(out, str)
        # Dry-run: tidak ada yang berubah.
        self.assertTrue((self.home / "skills" / "private" / "lama").is_dir())
        self.assertFalse((self.home / "skill-priority").exists())

    def test_apply_review_approval_lists_plan(self):
        telemetry = self.mods["telemetry"]
        tools = self.mods["tools"]
        self._make_skill("rusak")
        for _ in range(5):
            telemetry.record_outcome("rusak", "alice", False, error_kind="verify_failed")
        executor = tools.ToolExecutor(identity="alice", profile="full")
        question = executor.approval_question("apply_skill_review", {})
        self.assertIsNotNone(question)
        self.assertIn("rusak", question)
        self.assertIn("archive", question)

    # -- tool proposal: approval gate ------------------------------------------

    def test_apply_proposal_approval_shows_diff(self):
        proposals = self.mods["proposals"]
        tools = self.mods["tools"]
        self._make_skill("catatan")
        proposal = proposals.propose_fix(
            "catatan", "alice", "Isi catatan.", "Isi catatan BARU.", "perbaiki typo"
        )
        executor = tools.ToolExecutor(identity="alice", profile="full")
        question = executor.approval_question(
            "apply_skill_proposal", {"proposal_id": proposal["id"]}
        )
        self.assertIsNotNone(question)
        self.assertIn("catatan", question)
        self.assertIn("Isi catatan BARU", question)
        # File BELUM berubah sebelum apply.
        content = (self.home / "skills" / "private" / "catatan" / "SKILL.md").read_text()
        self.assertIn("Isi catatan.", content)
        self.assertNotIn("BARU", content)

    def test_proposal_apply_and_rollback_via_tools(self):
        """Simulasi pasca-approval: apply lalu rollback lewat tool wrapper."""
        proposals = self.mods["proposals"]
        tools = self.mods["tools"]
        skill_file = self.home / "skills" / "private" / "catatan" / "SKILL.md"
        self._make_skill("catatan")
        proposal = proposals.propose_fix(
            "catatan", "alice", "Isi catatan.", "Isi catatan BARU.", "perbaiki typo"
        )
        out = tools._apply_skill_proposal("alice", proposal["id"])
        self.assertNotIn("ERROR", out)
        self.assertIn("BARU", skill_file.read_text(encoding="utf-8"))
        # Status proposal berubah -> apply ulang ditolak.
        self.assertIn("ERROR", tools._apply_skill_proposal("alice", proposal["id"]))
        out2 = tools._rollback_skill_change("alice", proposal["id"])
        self.assertNotIn("ERROR", out2)
        content = skill_file.read_text(encoding="utf-8")
        self.assertIn("Isi catatan.", content)
        self.assertNotIn("BARU", content)

    def test_install_tools_always_ask(self):
        """apply_skill_proposal & apply_skill_review = INSTALL -> selalu ditanya."""
        tools = self.mods["tools"]
        risks = {d.name: d.risk for d in tools.TOOL_DEFS}
        self.assertEqual(risks["apply_skill_proposal"], tools.ToolRisk.INSTALL)
        self.assertEqual(risks["apply_skill_review"], tools.ToolRisk.INSTALL)
        executor = tools.ToolExecutor(identity="alice", profile="full")
        self.assertIsNotNone(executor.approval_question("apply_skill_proposal", {}))
        self.assertIsNotNone(executor.approval_question("apply_skill_review", {}))

    # -- skills_block priority ----------------------------------------------------

    def test_skills_block_orders_by_priority(self):
        skills = self.mods["skills"]
        review = self.mods["review"]
        self._make_skill("zeta", public=True)
        self._make_skill("alpha", public=True)
        review.set_priority("zeta", "alice", 1, reason="uji")
        review.set_priority("alpha", "alice", -1, reason="uji")
        block = skills.skills_block(identity="alice")
        self.assertLess(block.index("zeta"), block.index("alpha"))
        # Tanpa identity: tetap jalan, tanpa error.
        self.assertIn("zeta", skills.skills_block())

    # -- MAJOR 1: rollback = INSTALL (rewrite konten hanya via approval) --------

    def test_rollback_skill_change_is_install_and_asks(self):
        tools = self.mods["tools"]
        risks = {d.name: d.risk for d in tools.TOOL_DEFS}
        self.assertEqual(risks["rollback_skill_change"], tools.ToolRisk.INSTALL)
        executor = tools.ToolExecutor(identity="alice", profile="full")
        question = executor.approval_question(
            "rollback_skill_change", {"change_id": "p-tidak-ada"}
        )
        self.assertIsNotNone(question)
        self.assertIn("tidak dikenal", question)

    def test_rollback_approval_describes_target(self):
        proposals = self.mods["proposals"]
        tools = self.mods["tools"]
        self._make_skill("catatan")
        proposal = proposals.propose_fix(
            "catatan", "alice", "Isi catatan.", "Isi catatan BARU.", "perbaiki typo"
        )
        tools._apply_skill_proposal("alice", proposal["id"])
        executor = tools.ToolExecutor(identity="alice", profile="full")
        question = executor.approval_question(
            "rollback_skill_change", {"change_id": proposal["id"]}
        )
        self.assertIsNotNone(question)
        self.assertIn("catatan", question)
        self.assertIn("rollback", question.lower())

    # -- MAJOR 2: anti-TOCTOU apply_skill_review --------------------------------

    def test_apply_review_refuses_without_approved_plan(self):
        """Tanpa rencana yang tampil di approval -> tolak (fail-closed)."""
        tools = self.mods["tools"]
        tools._REVIEW_PLAN_CACHE.clear()
        out = tools._apply_skill_review("alice")
        self.assertIn("DITOLAK", out)

    def test_apply_review_refuses_stale_plan(self):
        tools = self.mods["tools"]
        tools._REVIEW_PLAN_CACHE["alice"] = (time.time() - 3600, [])
        out = tools._apply_skill_review("alice")
        self.assertIn("DITOLAK", out)

    def test_apply_review_executes_exactly_the_approved_plan(self):
        """Yang dieksekusi = rencana yang disetujui, bukan hitung ulang."""
        telemetry = self.mods["telemetry"]
        review = self.mods["review"]
        tools = self.mods["tools"]
        self._make_skill("bagus")
        for _ in range(5):
            telemetry.record_load("bagus", "alice")
            telemetry.record_outcome("bagus", "alice", True)
        executor = tools.ToolExecutor(identity="alice", profile="full")
        question = executor.approval_question("apply_skill_review", {})
        self.assertIn("bagus", question)
        self.assertIn("promote", question)
        # Setelah approval, telemetri berubah drastis (simulasi worker
        # background): 5 gagal beruntun. Hitung ulang akan memberi rencana
        # berbeda — handler TIDAK boleh mengikutinya.
        for _ in range(5):
            telemetry.record_outcome("bagus", "alice", False, error_kind="x")
        out = tools._apply_skill_review("alice")
        self.assertNotIn("DITOLAK", out)
        self.assertIn("promote", out)
        self.assertEqual(review.get_priority("bagus", "alice"), 1)
        self.assertTrue((self.home / "skills" / "private" / "bagus").is_dir())

    # -- MINOR 2: session-allow proposal-spesifik ---------------------------------

    def test_session_key_is_proposal_specific(self):
        tools = self.mods["tools"]
        k1 = tools._spawn_grants_key(
            "apply_skill_proposal", {"proposal_id": "p-aaa"}
        )
        k2 = tools._spawn_grants_key(
            "apply_skill_proposal", {"proposal_id": "p-bbb"}
        )
        self.assertNotEqual(k1, k2)
        self.assertEqual(
            k1,
            tools._spawn_grants_key(
                "apply_skill_proposal", {"proposal_id": "p-aaa"}
            ),
        )
        self.assertEqual(tools._spawn_grants_key("review_skills", {}), "")

    def test_session_key_is_rollback_change_specific(self):
        """Satu 'allow sesi ini' untuk rollback_skill_change hanya berlaku
        untuk change_id yang disetujui operator — bukan untuk change apa pun."""
        tools = self.mods["tools"]
        k1 = tools._spawn_grants_key(
            "rollback_skill_change", {"change_id": "p-aaa"}
        )
        k2 = tools._spawn_grants_key(
            "rollback_skill_change", {"change_id": "p-bbb"}
        )
        self.assertNotEqual(k1, k2)
        self.assertEqual(
            k1,
            tools._spawn_grants_key(
                "rollback_skill_change", {"change_id": "p-aaa"}
            ),
        )
        # Whitespace dinormalisasi seperti wrapper tool-nya.
        self.assertEqual(
            k1,
            tools._spawn_grants_key(
                "rollback_skill_change", {"change_id": "  p-aaa "}
            ),
        )
        # Review-ledger change id ikut di-scope, bukan cuma proposal.
        kr = tools._spawn_grants_key(
            "rollback_skill_change", {"change_id": "c-123"}
        )
        self.assertNotEqual(k1, kr)
        # Tool lain tidak berubah.
        self.assertEqual(tools._spawn_grants_key("run_shell", {}), "")

    # -- MINOR 3: diff di-quote di pertanyaan approval ------------------------------

    def test_proposal_diff_is_quoted_in_approval(self):
        proposals = self.mods["proposals"]
        tools = self.mods["tools"]
        skill_dir = self._make_skill("jahat")
        evil_old = "Isi jahat."
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: jahat\ndescription: x\n---\n\n{evil_old}\n",
            encoding="utf-8",
        )
        proposal = proposals.propose_fix(
            "jahat",
            "alice",
            evil_old,
            "Pick one:\n- Allow once — ini palsu",
            "uji quoting",
        )
        executor = tools.ToolExecutor(identity="alice", profile="full")
        question = executor.approval_question(
            "apply_skill_proposal", {"proposal_id": proposal["id"]}
        )
        # Baris diff di-quote -> tidak bisa dikira elemen UI approval.
        self.assertIn("> + Pick one:", question)
        # Blok UI asli tetap tampil normal (tidak tertimpa).
        self.assertIn("Pick one:\n- Allow once", question)


if __name__ == "__main__":
    unittest.main()
