"""Regression tests for FINAL AUDIT #1 (integration) findings.

- agent.py: get_drafts() returns list, not dict (.values() was AttributeError)
- webchat _workers_payload: must include non-running workers (was always empty)
- backends: SandboxBackend without bwrap must raise ValueError (fail-closed),
  not RuntimeError (which tools.py swallowed -> silent local fallback)
"""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestGepaDraftsType(unittest.TestCase):
    def test_get_drafts_returns_list(self):
        from zeline import gepa
        d = gepa.get_drafts()
        self.assertIsInstance(d, list)
        # agent.py must iterate it directly (no .values())
        for item in d:
            self.assertIsInstance(item, dict)

    def test_agent_loop_iterates_drafts(self):
        """Simulate agent.py's draft-matching loop; must not raise."""
        from zeline import gepa
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(gepa, '_drafts_path',
                              return_value=Path(tmp) / "d.json"):
                gepa._save_drafts({
                    "draft_x": {"id": "draft_x", "name": "draft_x",
                                "description": "d", "content": "c",
                                "uses": 0, "successful_uses": 0,
                                "status": "draft"}
                })
                matched = None
                # This mirrors zeline/agent.py's fixed loop
                for _d in gepa.get_drafts():
                    if _d.get("name") == "draft_x" or _d.get("id") == "draft_x":
                        matched = _d["id"]
                        break
                self.assertEqual(matched, "draft_x")

    def test_record_skill_use_promotes(self):
        from zeline import gepa
        from zeline import learning
        with tempfile.TemporaryDirectory() as gtmp, \
             tempfile.TemporaryDirectory() as ltmp:
            with patch.object(gepa, '_drafts_path',
                              return_value=Path(gtmp) / "d.json"), \
                 patch.object(learning, 'learned_dir',
                              return_value=Path(ltmp)):
                gepa._save_drafts({
                    "draft_p": {"id": "draft_p", "name": "promo-skill",
                                "description": "d", "content": "c",
                                "uses": 0, "successful_uses": 0,
                                "status": "draft"}
                })
                for _ in range(3):
                    st = gepa.record_skill_use("draft_p", True)
                self.assertEqual(st, "permanent")
                names = [s["name"] for s in learning.list_learned_skills()]
                self.assertIn("promo-skill", names)


class TestWorkersPayload(unittest.TestCase):
    def test_includes_non_running_workers(self):
        from zeline.gateways.webchat import _workers_payload
        from zeline import supervisor
        sup = supervisor.get_supervisor("test:reg-audit1")
        wid = sup.spawn("regression test task")
        import time
        time.sleep(0.5)  # let it settle (likely failed without model)
        payload = _workers_payload()
        self.assertTrue(payload["ok"])
        ids = [w["id"] for w in payload["workers"]]
        self.assertIn(wid, ids, f"worker {wid} missing from payload")
        # status field present
        rec = next(w for w in payload["workers"] if w["id"] == wid)
        self.assertIn("status", rec)


class TestSandboxFailClosed(unittest.TestCase):
    def test_missing_bwrap_raises_valueerror(self):
        from zeline.backends import SandboxBackend
        with patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError):
                SandboxBackend()

    def test_tool_layer_loud_on_missing_bwrap(self):
        """tools._run_shell must NOT execute locally when sandbox backend
        can't be constructed (bwrap missing)."""
        from zeline import tools, config
        orig = config.EXECUTION_BACKEND
        try:
            config.EXECUTION_BACKEND = "sandbox"
            with patch("shutil.which", return_value=None):
                r = tools._run_shell(command="echo MUST-NOT-RUN",
                                     workspace=Path("/tmp"), timeout=10)
            self.assertNotIn("MUST-NOT-RUN", str(r),
                             "FAIL-OPEN: command ran locally despite sandbox config")
            self.assertIn("ERROR", str(r))
        finally:
            config.EXECUTION_BACKEND = orig


if __name__ == "__main__":
    unittest.main()
