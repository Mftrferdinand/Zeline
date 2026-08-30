"""Contract tests for parallel delegation, roles, and the verifier pass.

The feature trades API concurrency for wall-clock time, so the tests pin both
halves: that work really runs in parallel, and that going parallel never costs
the caller a result.

Four invariants:

- **Parallel means parallel.** Three tasks that each sleep finish in about the
  time of one, not three.
- **One failure never discards a sibling's finished work.** Losing completed
  output because a neighbour crashed would be the worst possible trade.
- **Each worker gets a distinct identity.** Workers each build a MemoryStore
  keyed by identity, so a shared identity means concurrent writes to one file.
- **A verifier can improve an answer but never destroy one.** If verification
  fails, the work is still returned, explicitly labelled unchecked.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    delegation = importlib.import_module("zeline.delegation")
    tools = importlib.import_module("zeline.tools")
    return cfg, delegation, tools


class DelegationBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self.home.mkdir(parents=True)
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.delegation, self.tools = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()

    def tasks(self, count: int):
        return [self.delegation.Task(goal=f"task {i}") for i in range(count)]


class ParsingTests(DelegationBase):
    def test_a_list_of_objects_parses(self):
        parsed, error = self.delegation.parse_tasks([
            {"goal": "one"}, {"goal": "two", "role": "coder", "context": "ctx"},
        ])
        self.assertIsNone(error)
        self.assertEqual([t.goal for t in parsed], ["one", "two"])
        self.assertEqual(parsed[1].clean_role, "coder")
        self.assertEqual(parsed[1].context, "ctx")

    def test_a_json_string_is_accepted(self):
        """Models sometimes send the array as a JSON string; rejecting it costs a retry."""
        parsed, error = self.delegation.parse_tasks('[{"goal": "from json"}]')
        self.assertIsNone(error)
        self.assertEqual(parsed[0].goal, "from json")

    def test_a_bare_string_is_treated_as_the_goal(self):
        parsed, error = self.delegation.parse_tasks(["just do this"])
        self.assertIsNone(error)
        self.assertEqual(parsed[0].goal, "just do this")

    def test_an_empty_list_is_an_error(self):
        _, error = self.delegation.parse_tasks([])
        self.assertIn("non-empty list", error)

    def test_a_task_without_a_goal_names_its_position(self):
        _, error = self.delegation.parse_tasks([{"goal": "ok"}, {"context": "oops"}])
        self.assertIn("task 2", error)

    def test_malformed_json_is_reported(self):
        _, error = self.delegation.parse_tasks("{not json")
        self.assertIn("tasks must be", error)

    def test_a_non_list_is_reported(self):
        _, error = self.delegation.parse_tasks({"goal": "not a list"})
        self.assertIn("non-empty list", error)

    def test_an_unknown_role_falls_back_to_worker(self):
        """A typo'd role should not fail the task; it just gets no extra brief."""
        task = self.delegation.Task(goal="x", role="astronaut")
        self.assertEqual(task.clean_role, "worker")

    def test_roles_are_case_insensitive(self):
        self.assertEqual(self.delegation.Task(goal="x", role="CODER").clean_role, "coder")


class ParallelismTests(DelegationBase):
    def test_tasks_really_run_in_parallel(self):
        """The whole point: three 0.4s tasks must not take 1.2s."""
        def slow(task, index):
            time.sleep(0.4)
            return f"done {task.goal}"

        start = time.monotonic()
        results = self.delegation.run_tasks(self.tasks(3), spawn=slow)
        elapsed = time.monotonic() - start
        self.assertTrue(all(item.ok for item in results))
        self.assertLess(elapsed, 0.9, f"took {elapsed:.2f}s — looks sequential")

    def test_a_single_task_uses_no_pool(self):
        seen: list[str] = []

        def record(task, index):
            seen.append(threading.current_thread().name)
            return "ok"

        self.delegation.run_tasks(self.tasks(1), spawn=record)
        self.assertEqual(seen, [threading.current_thread().name])

    def test_concurrency_is_capped(self):
        """Each worker is a full agent; unbounded fan-out would hammer the API."""
        peak = {"value": 0}
        current = {"value": 0}
        lock = threading.Lock()

        def counted(task, index):
            with lock:
                current["value"] += 1
                peak["value"] = max(peak["value"], current["value"])
            time.sleep(0.15)
            with lock:
                current["value"] -= 1
            return "ok"

        self.delegation.run_tasks(self.tasks(8), spawn=counted, limit=2)
        self.assertLessEqual(peak["value"], 2)

    def test_the_cap_comes_from_config(self):
        saved = self.config.config_copy()
        saved["tools"]["max_parallel_subagents"] = 5
        self.config.save_config(saved)
        self.assertEqual(self.delegation.max_parallel(), 5)

    def test_a_nonsense_cap_is_clamped_not_fatal(self):
        for value in ("many", 0, -3, 999):
            with self.subTest(value=value):
                saved = self.config.config_copy()
                saved["tools"]["max_parallel_subagents"] = value
                self.config.save_config(saved)
                self.assertGreaterEqual(self.delegation.max_parallel(), 1)
                self.assertLessEqual(self.delegation.max_parallel(), 8)

    def test_results_keep_their_original_order(self):
        """Completion order is nondeterministic; reported order must not be."""
        def variable(task, index):
            time.sleep(0.05 * (3 - index))
            return f"result {index}"

        results = self.delegation.run_tasks(self.tasks(3), spawn=variable)
        self.assertEqual([item.index for item in results], [0, 1, 2])

    def test_no_tasks_returns_nothing_rather_than_erroring(self):
        self.assertEqual(self.delegation.run_tasks([], spawn=lambda t, i: "x"), [])


class FailureTests(DelegationBase):
    def test_one_failure_does_not_discard_the_others(self):
        def flaky(task, index):
            if index == 1:
                raise RuntimeError("worker exploded")
            return f"ok {index}"

        results = self.delegation.run_tasks(self.tasks(3), spawn=flaky)
        self.assertEqual([item.ok for item in results], [True, False, True])
        rendered = self.delegation.render(results)
        self.assertIn("ok 0", rendered)
        self.assertIn("ok 2", rendered)
        self.assertIn("worker exploded", rendered)

    def test_the_render_states_how_many_succeeded(self):
        def flaky(task, index):
            if index == 0:
                raise RuntimeError("nope")
            return "fine"

        rendered = self.delegation.render(self.delegation.run_tasks(self.tasks(2), spawn=flaky))
        self.assertIn("1/2", rendered)
        self.assertIn("do not assume the failed ones were done", rendered)

    def test_every_task_failing_is_still_reported(self):
        def always_fails(task, index):
            raise RuntimeError("all down")

        rendered = self.delegation.render(self.delegation.run_tasks(self.tasks(2), spawn=always_fails))
        self.assertIn("0/2", rendered)

    def test_empty_output_is_labelled_not_silently_blank(self):
        results = self.delegation.run_tasks(self.tasks(1), spawn=lambda t, i: "   ")
        self.assertIn("no output", results[0].output)


class RenderTests(DelegationBase):
    def test_a_single_unverified_task_keeps_the_old_shape(self):
        """Existing callers and prompts expect this exact prefix."""
        results = self.delegation.run_tasks(self.tasks(1), spawn=lambda t, i: "the answer")
        self.assertEqual(self.delegation.render(results), "[sub-agent result]\nthe answer")

    def test_a_single_failed_task_keeps_the_old_error_shape(self):
        def boom(task, index):
            raise RuntimeError("bad")

        rendered = self.delegation.render(self.delegation.run_tasks(self.tasks(1), spawn=boom))
        self.assertTrue(rendered.startswith("ERROR: sub-agent failed:"))

    def test_roles_are_labelled_in_multi_task_output(self):
        tasks = [
            self.delegation.Task(goal="find it", role="researcher"),
            self.delegation.Task(goal="fix it", role="coder"),
        ]
        rendered = self.delegation.render(
            self.delegation.run_tasks(tasks, spawn=lambda t, i: f"did {t.goal}")
        )
        self.assertIn("(researcher)", rendered)
        self.assertIn("(coder)", rendered)

    def test_verification_is_appended_when_present(self):
        results = self.delegation.run_tasks(self.tasks(1), spawn=lambda t, i: "work")
        rendered = self.delegation.render(results, verification="VERDICT: ok")
        self.assertIn("--- verification", rendered)
        self.assertIn("VERDICT: ok", rendered)

    def test_a_failed_verification_says_the_work_is_unchecked(self):
        """Silence about whether an answer was checked is worse than either answer."""
        results = self.delegation.run_tasks(self.tasks(1), spawn=lambda t, i: "work")
        rendered = self.delegation.render(results, verified=False)
        self.assertIn("work", rendered)
        self.assertIn("unchecked", rendered)

    def test_no_results_is_an_explicit_error(self):
        self.assertIn("ERROR", self.delegation.render([]))


class BriefTests(DelegationBase):
    def test_context_is_appended_with_an_explicit_warning(self):
        task = self.delegation.Task(goal="do it", context="path=/tmp/x")
        brief = self.delegation.build_brief(task)
        self.assertIn("do it", brief)
        self.assertIn("path=/tmp/x", brief)
        self.assertIn("no other memory", brief)

    def test_no_context_means_the_goal_alone(self):
        self.assertEqual(self.delegation.build_brief(self.delegation.Task(goal="bare")), "bare")

    def test_every_role_has_a_distinct_brief(self):
        briefs = {
            role: self.delegation.system_extra_for(role)
            for role in self.delegation.ROLE_BRIEFS
        }
        self.assertEqual(len(set(briefs.values())), len(briefs))
        for role, text in briefs.items():
            with self.subTest(role=role):
                self.assertIn("SUB-AGENT", text)
                if role != "worker":
                    self.assertIn(role.upper(), text)

    def test_the_verifier_brief_asks_for_a_verdict(self):
        text = self.delegation.system_extra_for("reviewer", verifier=True)
        self.assertIn("VERDICT", text)
        self.assertIn("VERIFIER", text)

    def test_verification_material_carries_goal_and_output(self):
        results = self.delegation.run_tasks(
            [self.delegation.Task(goal="find X", role="researcher")],
            spawn=lambda t, i: "X is 42",
        )
        material = self.delegation.verification_material(results, "the overall goal")
        self.assertIn("the overall goal", material)
        self.assertIn("X is 42", material)
        self.assertIn("researcher", material)

    def test_verification_material_omits_failed_tasks(self):
        """Asking the verifier to check an error message wastes the pass."""
        def flaky(task, index):
            if index == 0:
                raise RuntimeError("failed one")
            return "good output"

        results = self.delegation.run_tasks(self.tasks(2), spawn=flaky)
        material = self.delegation.verification_material(results, "goal")
        self.assertIn("good output", material)
        self.assertNotIn("failed one", material)


class ExecutorTests(DelegationBase):
    def executor(self, profile: str = "full", depth: int = 0):
        return self.tools.ToolExecutor(
            identity="cli:local", profile=profile, workspace=str(self.home), depth=depth
        )

    def spawned(self, executor, outputs=None, fail_on=None):
        """Replace real sub-agents with a recorder, keeping the real plumbing."""
        calls: list[dict] = []

        def fake(brief, system_extra, suffix):
            calls.append({"brief": brief, "extra": system_extra, "suffix": suffix})
            if fail_on and fail_on(brief, suffix):
                raise RuntimeError("sub-agent died")
            if outputs:
                return outputs(brief, suffix)
            return f"result for {suffix or 'single'}"

        executor._spawn_subagent = fake
        return calls

    def test_a_single_goal_still_works(self):
        executor = self.executor()
        self.spawned(executor)
        result = executor.run("delegate_task", {"goal": "do one thing"})
        self.assertIn("[sub-agent result]", result)

    def test_missing_goal_and_tasks_is_reported(self):
        self.assertIn("non-empty goal", self.executor().run("delegate_task", {}))

    def test_several_tasks_each_get_a_sub_agent(self):
        executor = self.executor()
        calls = self.spawned(executor)
        result = executor.run("delegate_task", {"tasks": [
            {"goal": "alpha"}, {"goal": "beta"}, {"goal": "gamma"},
        ]})
        self.assertEqual(len(calls), 3)
        self.assertIn("3/3", result)
        for goal in ("alpha", "beta", "gamma"):
            self.assertIn(goal, result)

    def test_parallel_workers_get_distinct_identities(self):
        """They each build a MemoryStore keyed by identity — sharing one means
        concurrent writes to the same file."""
        executor = self.executor()
        calls = self.spawned(executor)
        executor.run("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}]})
        suffixes = [call["suffix"] for call in calls]
        self.assertEqual(len(set(suffixes)), len(suffixes))

    def test_roles_reach_the_sub_agent_prompt(self):
        executor = self.executor()
        calls = self.spawned(executor)
        executor.run("delegate_task", {"tasks": [
            {"goal": "research it", "role": "researcher"},
            {"goal": "code it", "role": "coder"},
        ]})
        extras = " ".join(call["extra"] for call in calls)
        self.assertIn("RESEARCHER", extras)
        self.assertIn("CODER", extras)

    def test_context_reaches_the_sub_agent_brief(self):
        executor = self.executor()
        calls = self.spawned(executor)
        executor.run("delegate_task", {"goal": "fix it", "context": "file=/srv/app.py"})
        self.assertIn("file=/srv/app.py", calls[0]["brief"])

    def test_verify_adds_exactly_one_extra_sub_agent(self):
        executor = self.executor()
        calls = self.spawned(executor, outputs=lambda brief, suffix: (
            "VERDICT: problems\n1. the number is unproven"
            if suffix == "-verify" else "the answer is 42"
        ))
        result = executor.run("delegate_task", {"goal": "compute it", "verify": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1]["suffix"], "-verify")
        self.assertIn("VERDICT: problems", result)
        self.assertIn("the answer is 42", result)

    def test_the_verifier_is_given_the_work_to_check(self):
        executor = self.executor()
        calls = self.spawned(executor, outputs=lambda brief, suffix: (
            "VERDICT: ok" if suffix == "-verify" else "found the bug in parser.py"
        ))
        executor.run("delegate_task", {"goal": "find the bug", "verify": True})
        verifier_brief = calls[-1]["brief"]
        self.assertIn("found the bug in parser.py", verifier_brief)
        self.assertIn("find the bug", verifier_brief)

    def test_a_failing_verifier_still_returns_the_work(self):
        """Verification improves an answer; it must not be able to destroy one."""
        executor = self.executor()
        self.spawned(
            executor,
            outputs=lambda brief, suffix: "valuable work",
            fail_on=lambda brief, suffix: suffix == "-verify",
        )
        result = executor.run("delegate_task", {"goal": "do it", "verify": True})
        self.assertIn("valuable work", result)
        self.assertIn("unchecked", result)

    def test_an_empty_verdict_is_treated_as_unchecked(self):
        executor = self.executor()
        self.spawned(executor, outputs=lambda brief, suffix: (
            "" if suffix == "-verify" else "real work"
        ))
        result = executor.run("delegate_task", {"goal": "do it", "verify": True})
        self.assertIn("real work", result)
        self.assertIn("unchecked", result)

    def test_verification_is_skipped_when_everything_failed(self):
        """There is nothing to check, so the pass would only cost a round trip."""
        executor = self.executor()
        calls = self.spawned(executor, fail_on=lambda brief, suffix: suffix != "-verify")
        executor.run("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}], "verify": True})
        self.assertNotIn("-verify", [call["suffix"] for call in calls])

    def test_malformed_tasks_are_reported_before_spawning_anything(self):
        executor = self.executor()
        calls = self.spawned(executor)
        result = executor.run("delegate_task", {"tasks": [{"context": "no goal"}]})
        self.assertIn("ERROR", result)
        self.assertEqual(calls, [])

    def test_a_subagent_cannot_delegate_further(self):
        """Depth limit still holds, so parallel fan-out cannot recurse."""
        names = [s["function"]["name"] for s in self.executor(depth=1).schemas]
        self.assertNotIn("delegate_task", names)

    def test_safe_profile_cannot_delegate(self):
        names = [s["function"]["name"] for s in self.executor("safe").schemas]
        self.assertNotIn("delegate_task", names)

    def test_the_schema_advertises_tasks_roles_and_verify(self):
        schema = next(
            s for s in self.executor().schemas if s["function"]["name"] == "delegate_task"
        )
        properties = schema["function"]["parameters"]["properties"]
        for key in ("goal", "context", "role", "tasks", "verify"):
            self.assertIn(key, properties)
        # goal must NOT be required any more, or 'tasks' alone would be rejected
        # by strict providers before it ever reached us.
        self.assertNotIn("required", schema["function"]["parameters"])

    def test_real_subagents_get_distinct_identities_and_bumped_depth(self):
        """Exercises the real _spawn_subagent wiring, faking only Zeline itself."""
        executor = self.executor()
        built: list[dict] = []

        class FakeZeline:
            def __init__(self, **kwargs):
                built.append(kwargs)

            def send(self, brief):
                return f"handled: {brief[:20]}"

        with mock.patch("zeline.agent.Zeline", FakeZeline):
            executor.run("delegate_task", {"tasks": [{"goal": "one"}, {"goal": "two"}]})
        identities = [item["identity"] for item in built]
        self.assertEqual(len(set(identities)), 2)
        self.assertTrue(all("::sub" in item for item in identities))
        self.assertTrue(all(item["depth"] == 1 for item in built))
        self.assertTrue(all(item["tool_profile"] == "full" for item in built))


if __name__ == "__main__":
    unittest.main()
