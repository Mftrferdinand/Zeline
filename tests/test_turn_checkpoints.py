"""Recovery snapshots preserve known results without replaying unknown effects."""
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class TurnCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {
            "ZELINE_HOME": str(self.home / "state"), "ZELINE_API_KEY": "fixture-only",
            "ZELINE_MODEL": "fixture", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.sessions = importlib.import_module("zeline.sessions")
        self.persistence = self.sessions.SessionPersistence()
        self.store = self.sessions.SessionStore(persistence=self.persistence)
        self.identity = "cli:checkpoint-fixture"
        self.session = self.store.get_or_create(self.identity, "full", workspace=str(self.home))

    @staticmethod
    def calls(*names):
        return {"role": "assistant", "content": "working", "tool_calls": [
            {"id": str(index), "type": "function", "function": {"name": name, "arguments": "{}"}}
            for index, name in enumerate(names)
        ]}

    def pending_count(self):
        with closing(self.persistence._connect()) as conn:
            return conn.execute("SELECT count(*) FROM turn_checkpoints").fetchone()[0]

    def test_actual_process_exit_preserves_first_result_and_uncertain_second(self):
        source = r'''
from pathlib import Path
import os
from zeline.sessions import SessionStore
store = SessionStore()
identity = 'cli:checkpoint-fixture'
session = store.get_or_create(identity, 'full', workspace=os.environ['FIXTURE_WORKSPACE'])
session.agent._call_llm = lambda **kw: {'role': 'assistant', 'content': 'working', 'tool_calls': [
 {'id': str(i), 'type': 'function', 'function': {'name': 'run_shell', 'arguments': '{}'}}
 for i in range(2)]}
count = 0
def execute(name, args):
 global count
 count += 1
 Path(os.environ['FIXTURE_WORKSPACE'], 'effect'+str(count)).write_text('committed')
 if count == 2:
  os._exit(73)
 return 'first effect completed'
session.agent.executor.run = execute
store.send(identity, 'perform two fixture effects', 'full')
'''
        env = dict(os.environ, FIXTURE_WORKSPACE=str(self.home),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        result = subprocess.run([sys.executable, "-c", source], env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        reopened = self.sessions.SessionStore()
        recovered = reopened.get_or_create(self.identity, "full", workspace=str(self.home))
        results = {m["tool_call_id"]: m["content"] for m in recovered.agent.messages if m.get("role") == "tool"}
        self.assertEqual(results["0"], "first effect completed")
        self.assertIn("UNKNOWN", results["1"])
        self.assertIn("Verify actual state", results["1"])
        self.assertTrue(any("[Zeline recovery]" in str(m.get("content")) for m in recovered.agent.messages))
        self.assertEqual(sorted(p.name for p in self.home.glob("effect*")), ["effect1", "effect2"])
        self.assertEqual(self.pending_count(), 1)
        self.assertEqual(self.persistence.load("someone-else"), ([], None))

    def test_predispatch_save_failure_prevents_all_tool_execution(self):
        agent = self.session.agent
        real_save = self.persistence.save_checkpoint
        count = 0
        def save(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("SECRET-PATH")
            return real_save(*args)
        with patch.object(agent, "_call_llm", return_value=self.calls("run_shell")), \
             patch.object(agent.executor, "run") as execute, \
             patch.object(self.persistence, "save_checkpoint", side_effect=save), \
             self.assertLogs("zeline.sessions", level="WARNING") as logs:
            with self.assertRaises(self.sessions.ZelineError) as error:
                self.store.send(self.identity, "fixture", "full")
        execute.assert_not_called()
        self.assertNotIn("SECRET-PATH", str(error.exception) + str(logs.output))
        self.assertFalse(self.session.running)

    def test_posteffect_save_failure_does_not_execute_remaining_tools(self):
        agent = self.session.agent
        real_save = self.persistence.save_checkpoint
        count = 0
        def save(*args):
            nonlocal count
            count += 1
            if count == 4:
                raise OSError("fixture")
            return real_save(*args)
        with patch.object(agent, "_call_llm", return_value=self.calls("run_shell", "run_shell")), \
             patch.object(agent.executor, "run", return_value="effect happened") as execute, \
             patch.object(self.persistence, "save_checkpoint", side_effect=save), \
             self.assertLogs("zeline.sessions", level="WARNING"):
            with self.assertRaises(self.sessions.ZelineError):
                self.store.send(self.identity, "fixture", "full")
        self.assertEqual(execute.call_count, 1)
        recovered = self.sessions.SessionStore().get_or_create(self.identity, "full")
        results = [m["content"] for m in recovered.agent.messages if m.get("role") == "tool"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all("UNKNOWN" in value for value in results))

    def test_successful_completion_clears_checkpoint_in_same_transaction(self):
        agent = self.session.agent
        with patch.object(agent, "_call_llm", side_effect=[self.calls("run_shell"), {"content": "done"}]), \
             patch.object(agent.executor, "run", return_value="verified fixture output"):
            self.assertEqual(self.store.send(self.identity, "fixture", "full"), "done")
        self.assertEqual(self.pending_count(), 0)
        history, title = self.persistence.load(self.identity)
        self.assertEqual(history[-1]["content"], "done")
        self.assertNotIn("[Zeline recovery]", str(history))
        with closing(self.persistence._connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM archive").fetchone()[0], 2)

    def test_final_commit_failure_retains_recovery_snapshot(self):
        with closing(self.persistence._connect()) as conn, conn:
            conn.execute("CREATE TRIGGER fail_answer BEFORE INSERT ON archive "
                         "WHEN NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with patch.object(self.session.agent, "_call_llm", return_value={"content": "done"}), \
             self.assertLogs("zeline.sessions", level="WARNING"):
            reply = self.store.send(self.identity, "fixture", "full")
        self.assertIn("gagal disimpan", reply)
        self.assertEqual(self.pending_count(), 1)
        self.assertIn("[Zeline recovery]", str(self.persistence.load(self.identity)))

    def test_reset_and_explicit_import_clear_pending_state(self):
        history = [{"role": "user", "content": "pending"}]
        self.persistence.save_checkpoint(self.identity, history)
        self.assertTrue(self.persistence.reset(self.identity))
        self.assertEqual(self.persistence.load(self.identity), ([], None))
        self.persistence.save_checkpoint(self.identity, history)
        replacement = [{"role": "user", "content": "imported"}]
        self.persistence.save(self.identity, replacement)
        self.assertEqual(self.persistence.load(self.identity), (replacement, None))
        self.assertEqual(self.pending_count(), 0)

    def test_inventory_includes_unfinished_first_turn_and_prefers_latest_snapshot(self):
        self.persistence.save_checkpoint(self.identity, [{"role": "user", "content": "first"}], "pending title")
        entries = self.persistence.list_sessions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "pending title")
        self.persistence.save(self.identity, [{"role": "user", "content": "committed"}], "old title")
        self.persistence.save_checkpoint(self.identity, [{"role": "user", "content": "pending"}], "new title")
        entries = self.persistence.list_sessions()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "new title")

    def test_corrupt_checkpoint_stops_restore_without_overwriting(self):
        self.persistence.save_checkpoint(self.identity, [{"role": "user", "content": "fixture"}])
        with closing(self.persistence._connect()) as conn, conn:
            conn.execute("UPDATE turn_checkpoints SET messages = 'invalid-json'")
        with self.assertLogs("zeline.sessions", level="WARNING"):
            with self.assertRaises(self.sessions.ZelineError):
                self.sessions.SessionStore().get_or_create(self.identity, "full")
        with closing(self.persistence._connect()) as conn:
            self.assertEqual(conn.execute("SELECT messages FROM turn_checkpoints").fetchone()[0], "invalid-json")

    def test_parallel_readonly_batch_records_results_before_ui_callback(self):
        agent = self.session.agent
        def ui(*args):
            history, _ = self.persistence.load(self.identity)
            self.assertEqual(len([m for m in history if m.get("role") == "tool"]), 2)
        with patch.object(agent, "_call_llm", side_effect=[self.calls("read_file", "read_file"), {"content": "done"}]), \
             patch.object(agent.executor, "run", return_value="read evidence"):
            self.assertEqual(self.store.send(self.identity, "fixture", "full", on_tool_result=ui), "done")


if __name__ == "__main__":
    unittest.main()
