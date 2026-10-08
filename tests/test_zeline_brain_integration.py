"""Integration test untuk wiring zeline-brain.

Memverifikasi REGISTRASI (bukan logika bisnis — itu sudah di-cover di
test_goals.py / test_memory_sync.py / test_deep_research_skill.py):

1. Lima ToolDef baru (goal_add, goal_update, goal_list, goal_get,
   sync_memory) terdaftar di tools.TOOL_DEFS dengan risk class yang benar.
2. Handler + wrapper bekerja end-to-end lewat ToolExecutor.run —
   termasuk unpack tuple (goal, note) di _goal_update.
3. sync_memory tool tidak crash saat konektor tidak terhubung.
4. goals.prompt_block_goals ter-wiring di perakitan system prompt agent.

   memory_sync TIDAK mendaftarkan cron job sendiri: modul ini murni fungsi
   sync dan tidak menyentuh scheduler — penjadwalan (bila ada) adalah
   keputusan operator lewat ``zeline cron add`` dengan pre-authorization
   eksplisit.
"""

from __future__ import annotations

import importlib
import os
import unittest
from pathlib import Path


class IntegrationBase(unittest.TestCase):
    def setUp(self):
        self._saved_home = os.environ.get("HOME")
        self._saved_zeline_home = os.environ.get("ZELINE_HOME")
        tmp = Path(self._tmp_dir())
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / ".zeline").mkdir(exist_ok=True)
        os.environ["HOME"] = str(tmp)
        os.environ.pop("ZELINE_HOME", None)
        self.home = tmp
        # Evict zeline modules agar config.DATA_DIR baca HOME yang baru.
        for name in [n for n in list(__import__("sys").modules) if n == "zeline" or n.startswith("zeline.")]:
            del __import__("sys").modules[name]

    def _tmp_dir(self):
        import tempfile

        if not hasattr(self, "_td"):
            self._td = tempfile.mkdtemp(prefix="zbrain-int-")
        return self._td

    def tearDown(self):
        # Bersihkan direktori /tmp/zbrain-int-* yang dibuat _tmp_dir():
        # mkdtemp tidak auto-hapus, dan test yang bocor meninggalkan sampah
        # di /tmp setiap kali suite dijalankan.
        if getattr(self, "_td", None):
            import shutil

            shutil.rmtree(self._td, ignore_errors=True)
        if self._saved_home is not None:
            os.environ["HOME"] = self._saved_home
        else:
            os.environ.pop("HOME", None)
        if self._saved_zeline_home is not None:
            os.environ["ZELINE_HOME"] = self._saved_zeline_home
        else:
            os.environ.pop("ZELINE_HOME", None)

    def fresh(self, name):
        return importlib.import_module(name)

    def executor_with_allow_all(self, tools_mod, ident="test:int"):
        """ToolExecutor dengan policy yang mengizinkan semua (test only).

        Tanpa policy, _approval_gate fail-closed menolak tool WRITE —
        itu perilaku produksi yang benar, tapi menghambat pengujian wiring.
        """
        ex = tools_mod.ToolExecutor(ident, profile="safe")

        class _AllowAll(tools_mod.ApprovalPolicy):
            def decide(self, executor, name, args):
                return "allow"

        ex.approval_policy = _AllowAll()
        return ex


class TestToolRegistration(IntegrationBase):
    def test_five_tooldefs_registered_with_correct_risk(self):
        tools = self.fresh("zeline.tools")
        by_name = {d.name: d for d in tools.TOOL_DEFS}
        expected = {
            "goal_add": tools.ToolRisk.WRITE,
            "goal_update": tools.ToolRisk.WRITE,
            "goal_list": tools.ToolRisk.READ,
            "goal_get": tools.ToolRisk.READ,
            "sync_memory": tools.ToolRisk.WRITE,
        }
        for name, risk in expected.items():
            self.assertIn(name, by_name, f"ToolDef {name} tidak terdaftar")
            self.assertEqual(by_name[name].risk, risk, f"risk {name} salah")

    def test_goal_tool_schemas_have_required_fields(self):
        tools = self.fresh("zeline.tools")
        by_name = {d.name: d for d in tools.TOOL_DEFS}
        self.assertEqual(set(by_name["goal_add"].parameters["required"]), {"title", "target"})
        self.assertEqual(by_name["goal_update"].parameters["required"], ["goal_id"])
        self.assertEqual(by_name["goal_get"].parameters["required"], ["goal_id"])

    def test_deep_research_tooldef_references_skill(self):
        tools = self.fresh("zeline.tools")
        by_name = {d.name: d for d in tools.TOOL_DEFS}
        self.assertIn("deep-research", by_name["deep_research"].description)


class TestGoalToolsEndToEnd(IntegrationBase):
    def test_add_list_update_get_roundtrip(self):
        tools = self.fresh("zeline.tools")
        ex = self.executor_with_allow_all(tools)
        out = ex.run("goal_add", {"title": "Lulus eval", "target": "$100k"})
        self.assertIn("Goal dibuat", out)
        listed = ex.run("goal_list", {})
        self.assertIn("Lulus eval", listed)
        goal_id = listed.split("—")[0].replace("•", "").strip()
        # update_goal mengembalikan tuple (goal, note): wrapper harus unpack,
        # model tidak boleh menerima repr tuple mentah.
        updated = ex.run("goal_update", {"goal_id": goal_id, "progress": 40})
        self.assertIn("40%", updated)
        self.assertNotIn("('", updated)
        detail = ex.run("goal_get", {"goal_id": goal_id})
        self.assertIn("Lulus eval", detail)
        self.assertIn("$100k", detail)

    def test_update_milestone_dict_converted(self):
        tools = self.fresh("zeline.tools")
        ex = self.executor_with_allow_all(tools)
        ex.run(
            "goal_add",
            {"title": "Baca buku", "target": "12 buku", "milestones": ["Buku 1"]},
        )
        listed = ex.run("goal_list", {})
        goal_id = listed.split("—")[0].replace("•", "").strip()
        out = ex.run(
            "goal_update",
            {"goal_id": goal_id, "milestone": {"key": 0, "done": True}},
        )
        self.assertIn("0%", out)  # progress tidak dipaksa naik
        detail = ex.run("goal_get", {"goal_id": goal_id})
        self.assertIn("✓", detail)


class TestSyncMemoryTool(IntegrationBase):
    def test_sync_memory_without_connectors_returns_summary(self):
        tools = self.fresh("zeline.tools")
        ex = self.executor_with_allow_all(tools)
        out = ex.run("sync_memory", {})
        # Tidak ada konektor terhubung di test env: harus ringkasan error
        # per source, bukan traceback/crash.
        self.assertIn("gmail", out)
        self.assertIn("ERROR", out)
        self.assertNotIn("Traceback", out)


class TestGoalsPromptWiring(IntegrationBase):
    def test_goal_appears_in_built_system_prompt(self):
        # Wiring PERILAKU (bukan teks source): goal aktif milik identity ini
        # harus muncul di system prompt yang benar-benar dirakit agent —
        # grep source akan lolos walau pemanggilannya dihapus/di-comment.
        goals = self.fresh("zeline.goals")
        goals.add_goal("test:int", "Lulus eval", "$100k")
        agent_module = self.fresh("zeline.agent")
        agent = agent_module.Zeline(identity="test:int", tool_profile="safe")
        prompt = agent._build_system_prompt()
        self.assertIn("Lulus eval", prompt)
        self.assertIn("0%", prompt)

    def test_no_goal_no_goals_section(self):
        agent_module = self.fresh("zeline.agent")
        agent = agent_module.Zeline(identity="test:int", tool_profile="safe")
        self.assertNotIn("Lulus eval", agent._build_system_prompt())

    def test_prompt_block_empty_when_no_goals(self):
        goals = self.fresh("zeline.goals")
        self.assertEqual(goals.prompt_block_goals("test:int"), "")

    def test_prompt_block_shows_active_goal(self):
        goals = self.fresh("zeline.goals")
        goals.add_goal("test:int", "Lulus eval", "$100k")
        block = goals.prompt_block_goals("test:int")
        self.assertIn("Lulus eval", block)
        self.assertIn("0%", block)


if __name__ == "__main__":
    unittest.main()
