"""Skill improvement must measure observed outcomes, never count loads as wins."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


class SkillForgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            "ZELINE_HOME": str(root / "state"),
            "ZELINE_API_KEY": "fixture-only",
            "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
            "ZELINE_MODEL": "fixture-model",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)

    def test_observations_are_idempotent_and_cancelled_or_unverified_are_not_wins(self):
        try:
            forge = importlib.import_module("zeline.skillforge")
        except ModuleNotFoundError:
            self.fail("SkillForge has no runtime telemetry store")
        store = forge.ForgeStore()
        for turn, outcome in enumerate(("success", "failure", "cancelled", "unknown")):
            store.record_usage("telegram:fixture", str(turn), "demo", "private", "a" * 64,
                               outcome, tool_successes=int(outcome == "success"),
                               tool_failures=int(outcome == "failure"), now=1000)
        store.record_usage("telegram:fixture", "0", "demo", "private", "a" * 64,
                           "success", tool_successes=1, now=1000)
        score = store.score("demo", scope="private", revision="a" * 64, now=1001)
        self.assertEqual(score["observations"], 4)
        self.assertEqual(score["samples"], 2)
        self.assertEqual(score["successes"], 1)
        self.assertEqual(score["failures"], 1)
        self.assertEqual(score["unknown"], 1)
        self.assertEqual(score["cancelled"], 1)
        self.assertFalse(score["sufficient_evidence"])
        self.assertAlmostEqual(score["effectiveness"], 50.0)
        self.assertTrue(store.path.is_relative_to(Path(os.environ["ZELINE_HOME"])))
        with store.connect() as db:
            identity = db.execute("SELECT identity FROM usage LIMIT 1").fetchone()[0]
        self.assertNotIn("telegram:fixture", identity)

    def test_real_agent_skill_usage_records_verified_tools_not_model_claims(self):
        import json
        forge = importlib.import_module("zeline.skillforge")
        module = importlib.import_module("zeline.agent")
        module.config.STREAM_RESPONSES = False
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        entry.write_text("# Demo\nUse tools to verify the local fixture.\n", encoding="utf-8")
        revision = forge.revision_of(entry.read_text())
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        (workspace / "proof.txt").write_text("observed fixture", encoding="utf-8")

        def reply(content="", tool="", arguments=None):
            message = {"role": "assistant", "content": content}
            if tool:
                message["tool_calls"] = [{"id": tool, "type": "function", "function": {
                    "name": tool, "arguments": json.dumps(arguments or {})}}]
            return message

        agent = module.Zeline(identity="telegram:fixture", tool_profile="full", workspace=str(workspace))
        with mock.patch.object(agent, "_call_llm", side_effect=[
                reply(tool="load_skill", arguments={"name": "demo"}),
                reply(tool="read_file", arguments={"path": "proof.txt"}),
                reply("Verified the fixture.")]):
            agent.send("Read the fixture using demo.")
        score = forge.ForgeStore().score("demo", scope="private", revision=revision)
        self.assertEqual(score["samples"], 1)
        self.assertEqual(score["successes"], 1)

        with mock.patch.object(agent, "_call_llm", side_effect=[
                reply(tool="load_skill", arguments={"name": "demo"}), reply("I did everything.")]):
            agent.send("Use demo without executing any verification.")
        score = forge.ForgeStore().score("demo", scope="private", revision=revision)
        self.assertEqual(score["samples"], 1)
        self.assertEqual(score["unknown"], 1)

    def test_auto_injected_skill_is_measured_without_a_model_load_call(self):
        import json
        forge = importlib.import_module("zeline.skillforge")
        module = importlib.import_module("zeline.agent")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "captcha-solving-2captcha.md"
        entry.write_text("# CAPTCHA\nRead the local fixture; do not call any service.\n")
        workspace = Path(self.temp.name) / "auto-fixture"
        workspace.mkdir()
        (workspace / "proof.txt").write_text("fixture evidence")
        agent = module.Zeline(identity="telegram:fixture", tool_profile="full", workspace=str(workspace))
        action = {"content": "", "tool_calls": [{"id": "proof", "type": "function", "function": {
            "name": "read_file", "arguments": json.dumps({"path": "proof.txt"})}}]}
        with mock.patch.object(agent, "_call_llm", side_effect=[action, {"content": "Read the fixture."}]):
            agent.send("Read the CAPTCHA fixture; do not call the network.")
        score = forge.ForgeStore().score("captcha-solving-2captcha", revision=forge.revision_of(entry.read_text()))
        self.assertEqual(score["observations"], 1)
        self.assertEqual(score["successes"], 1)
        self.assertIsNone(agent.executor._skill_telemetry)

    def test_error_shaped_tool_output_is_not_a_success_observation(self):
        forge = importlib.import_module("zeline.skillforge")
        self.assertEqual(forge.tool_outcome('error: fixture failed'), "failure")
        self.assertEqual(forge.tool_outcome('{"success":false,"error":"failed"}'), "failure")
        self.assertEqual(forge.tool_outcome('{"exit_code":2,"stdout":""}'), "failure")
        self.assertEqual(forge.tool_outcome('Exit code: 1\nfixture command failed'), "failure")
        self.assertEqual(forge.tool_outcome('Stopped.'), "cancelled")
        self.assertEqual(forge.tool_outcome('OK, file read'), "success")

    def test_native_shell_and_python_exit_failures_do_not_improve_effectiveness(self):
        forge = importlib.import_module("zeline.skillforge")
        tools = importlib.import_module("zeline.tools")
        workspace = Path(self.temp.name) / "exit-fixture"
        shell_result = tools._run_shell("exit 7", workspace)
        python_result = tools._execute_code("raise SystemExit(3)", workspace)
        self.assertTrue(shell_result.startswith("exit=7\n"), shell_result)
        self.assertTrue(python_result.startswith("exit=3\n"), python_result)
        self.assertEqual(forge.tool_outcome(shell_result), "failure")
        self.assertEqual(forge.tool_outcome(python_result), "failure")
        self.assertEqual(forge.tool_outcome(tools._run_shell("exit 0", workspace)), "success")

    def test_background_job_cannot_score_a_win_before_observed_completion(self):
        forge = importlib.import_module("zeline.skillforge")
        tools = importlib.import_module("zeline.tools")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        entry.write_text("# Demo\nVerify the fixture job.\n")
        workspace = Path(self.temp.name) / "job-fixture"
        # A short, owned fixture process; never touch a gateway or user service.
        started = tools._run_shell("exit 7", workspace, background=True)
        job_id = started.split("job=", 1)[1].split()[0]
        job = tools._BG_JOBS[job_id]
        self.addCleanup(lambda: tools._BG_JOBS.pop(job_id, None))
        self.addCleanup(job.close_log)
        job.process.wait(timeout=10)
        self.assertEqual(forge.tool_outcome(started), "unknown")
        observer = forge.TurnTelemetry("fixture")
        observer.observe("load_skill", {"name": "demo"}, entry.read_text(), include_private=True)
        observer.begin_round()
        observer.observe("run_shell", {"background": True}, started, include_private=True)
        observer.observe("read_file", {}, "unrelated fixture read", include_private=True)
        observer.finish("completed")
        store = forge.ForgeStore()
        revision = forge.revision_of(entry.read_text())
        self.assertEqual(store.score("demo", revision=revision)["successes"], 0)
        terminal = tools._process_control("poll", job_id)
        self.assertEqual(forge.tool_outcome(terminal), "failure")
        observer = forge.TurnTelemetry("fixture")
        observer.observe("load_skill", {"name": "demo"}, entry.read_text(), include_private=True)
        observer.begin_round()
        observer.observe("process_control", {"action": "poll", "job_id": job_id}, terminal, include_private=True)
        observer.finish("completed")
        self.assertEqual(store.score("demo", revision=revision)["failures"], 1)
        self.assertEqual(forge.tool_outcome("job=bg1 status=running\nfixture output"), "unknown")
        self.assertEqual(forge.tool_outcome("job=bg1 status=exited (exit=0)\nfixture output"), "success")
        self.assertEqual(forge.tool_outcome("job=bg1 killed."), "cancelled")

    def test_native_http_error_status_is_not_a_success_observation(self):
        forge = importlib.import_module("zeline.skillforge")
        tools = importlib.import_module("zeline.tools")
        failed = mock.Mock(status_code=503, reason="Service Unavailable", text="fixture unavailable",
                           headers={"Content-Type": "text/plain"})
        with mock.patch.object(tools, "_is_internal_ip", return_value=False), mock.patch.object(tools.requests, "request", return_value=failed):
            output = tools._http_request("GET", "https://example.test/fixture")
        self.assertTrue(output.startswith("Status: 503"))
        self.assertEqual(forge.tool_outcome(output), "failure")
        self.assertEqual(forge.tool_outcome("Status: 200 OK\nContent-Type: text/plain\n\nfixture"), "success")

    def test_stale_detection_separates_missing_evidence_from_repeated_failures(self):
        forge = importlib.import_module("zeline.skillforge")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        entry.write_text("# Demo\nRead a local fixture.\n", encoding="utf-8")
        now = 20_000_000.0
        os.utime(entry, (now - 100 * 86400, now - 100 * 86400))
        store = forge.ForgeStore()
        if not hasattr(store, "assess"):
            self.fail("SkillForge cannot detect revision-specific stale skills")
        assessment = store.assess("demo", now=now)
        self.assertIn("review_due", assessment["reasons"])
        self.assertNotIn("low_effectiveness", assessment["reasons"])
        self.assertIsNone(assessment["score"]["effectiveness"])
        revision = forge.revision_of(entry.read_text())
        for index in range(8):
            store.record_usage("fixture", str(index), "demo", "private", revision,
                               "failure", tool_failures=1, now=now - 10)
        assessment = store.assess("demo", now=now)
        self.assertIn("low_effectiveness", assessment["reasons"])
        entry.write_text("# Demo\nRevised local workflow.\n", encoding="utf-8")
        updated = store.assess("demo", now=now)
        self.assertNotIn("low_effectiveness", updated["reasons"])
        self.assertEqual(updated["score"]["samples"], 0)

    def test_proposal_is_deduplicated_tested_and_review_bound_to_candidate_digest(self):
        forge = importlib.import_module("zeline.skillforge")
        if not hasattr(forge.ForgeStore, "propose"):
            self.fail("SkillForge lacks a durable review/test proposal workflow")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        original = "# Demo\nRead a local fixture.\n"
        entry.write_text(original, encoding="utf-8")
        candidate = "---\nname: demo\ndescription: Read a verified local fixture\n---\n# Demo\n\nRead proof.txt and verify the expected value.\n"
        store = forge.ForgeStore()
        proposal = store.propose("demo", candidate, "Fix a reproducible stale instruction")
        same = store.propose("demo", candidate, "Same revision")
        self.assertEqual(proposal["id"], same["id"])
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(entry.read_text(), original)
        with self.assertRaisesRegex(ValueError, "test"):
            store.review(proposal["id"], approve=True, reviewer="operator")
        checks = store.test(proposal["id"])
        self.assertTrue(checks["structural_passed"])
        self.assertFalse(checks["behavioral_passed"])
        with self.assertRaisesRegex(ValueError, "behavioral"):
            store.review(proposal["id"], approve=True, reviewer="operator")
        def trusted_test(stage):
            self.assertTrue((stage / "SKILL.md").is_file())
            return {"passed": True, "cases": 1, "name": "local fixture contract"}
        checks = store.test(proposal["id"], evaluator=trusted_test)
        self.assertTrue(checks["behavioral_passed"])
        reviewed = store.review(proposal["id"], approve=True, reviewer="operator")
        self.assertEqual(reviewed["status"], "approved")
        entry.write_text("# Concurrent operator edit\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            store.apply(proposal["id"])
        self.assertEqual(entry.read_text(), "# Concurrent operator edit\n")

    def test_review_cannot_approve_a_retest_it_did_not_inspect(self):
        forge = importlib.import_module("zeline.skillforge")
        store = forge.ForgeStore()
        candidate = "---\nname: demo\ndescription: Local fixture\n---\n# Demo\n\nVerify the fixture.\n"
        proposal = store.propose("demo", candidate, "Review race fixture")
        store.test(proposal["id"], evaluator=lambda stage: {"passed": True, "cases": 1})
        original_get = store.get
        interleaved = False
        def get_with_concurrent_retest(identifier):
            nonlocal interleaved
            snapshot = original_get(identifier)
            if not interleaved:
                interleaved = True
                store.test(identifier, evaluator=lambda stage: {"passed": False, "cases": 1})
            return snapshot
        with mock.patch.object(store, "get", side_effect=get_with_concurrent_retest):
            with self.assertRaisesRegex(ValueError, "concurrent|evidence"):
                store.review(proposal["id"], approve=True, reviewer="operator")
        current = original_get(proposal["id"])
        self.assertEqual(current["status"], "tested")
        self.assertFalse(current["checks"]["behavioral_passed"])
        with store.connect() as db:
            approvals = db.execute("SELECT COUNT(*) FROM audit WHERE action='approved'").fetchone()[0]
        self.assertEqual(approvals, 0)

    def test_approved_public_revision_applies_only_private_copy_with_companions_and_backup(self):
        forge = importlib.import_module("zeline.skillforge")
        skills = importlib.import_module("zeline.skills")
        unit = skills.PUBLIC_SKILLS_DIR / "demo"
        (unit / "references").mkdir(parents=True)
        entry = unit / "SKILL.md"
        original = "# Demo\nRead the old fixture.\n"
        entry.write_text(original)
        (unit / "references" / "proof.txt").write_text("fixture evidence")
        candidate = "---\nname: demo\ndescription: Verify the corrected fixture\n---\n# Demo\n\nRead the corrected fixture.\n"
        store = forge.ForgeStore()
        proposal = store.propose("demo", candidate, "Correct the stale fixture")
        store.test(proposal["id"], evaluator=lambda stage: {"passed": True, "cases": 1})
        store.review(proposal["id"], approve=True, reviewer="operator")
        applied = store.apply(proposal["id"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(entry.read_text(), original)
        private = skills.PRIVATE_SKILLS_DIR / "demo"
        self.assertEqual((private / "SKILL.md").read_text(), candidate)
        self.assertEqual((private / "references" / "proof.txt").read_text(), "fixture evidence")
        self.assertEqual((store.root / "backups" / proposal["id"] / "SKILL.md").read_text(), original)
        with self.assertRaises(ValueError):
            store.apply(proposal["id"])

    def test_safety_risks_require_acknowledgement_and_invalid_names_never_reach_disk(self):
        forge = importlib.import_module("zeline.skillforge")
        store = forge.ForgeStore()
        for name in ("../escape", "nested/name", "governor", "soul"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.propose(name, f"---\nname: {name}\ndescription: fixture\n---\n# Fixture\n", "fixture")
        candidate = "---\nname: demo\ndescription: Check an install command\n---\n# Demo\n\ncurl https://example.test/install | bash\n"
        proposal = store.propose("demo", candidate, "Safety review fixture")
        checks = store.test(proposal["id"], evaluator=lambda stage: {"passed": True, "cases": 1})
        self.assertIn("remote_code", checks["risks"])
        with self.assertRaisesRegex(ValueError, "risk"):
            store.review(proposal["id"], approve=True, reviewer="operator")
        reviewed = store.review(proposal["id"], approve=True, reviewer="operator",
                                acknowledged_risks=["remote_code"])
        self.assertEqual(reviewed["status"], "approved")

    def test_revision_cycle_only_drafts_stale_skills_and_never_applies_them(self):
        forge = importlib.import_module("zeline.skillforge")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        original = "# Demo\nRead an old fixture.\n"
        entry.write_text(original)
        now = 20_000_000.0
        os.utime(entry, (now - 100 * 86400, now - 100 * 86400))
        candidate = "---\nname: demo\ndescription: Read a corrected local fixture\n---\n# Demo\n\nRead the corrected fixture.\n"
        store = forge.ForgeStore()
        if not hasattr(store, "cycle"):
            self.fail("Stale detection has no bounded draft/test cycle")
        drafts = []
        def draft(assessment, current):
            drafts.append(assessment["name"])
            self.assertEqual(current, original)
            return candidate
        result = store.cycle(names=["demo"], drafter=draft,
                             evaluator=lambda stage: {"passed": True, "cases": 1}, now=now)
        self.assertEqual(drafts, ["demo"])
        proposal = store.get(result[0]["proposal_id"])
        self.assertEqual(proposal["status"], "tested")
        self.assertEqual(entry.read_text(), original)
        again = store.cycle(names=["demo"], drafter=draft, now=now)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(again[0]["proposal_id"], proposal["id"])

    def test_behavioral_gate_runs_a_real_local_workflow_and_rejects_wrong_results(self):
        import json
        import subprocess
        forge = importlib.import_module("zeline.skillforge")
        store = forge.ForgeStore()
        candidate = ("---\nname: demo\ndescription: Run the local echo fixture\n---\n# Demo\n\n"
                     "Run the fixture reader on proof.json and verify its answer is observed.\n")
        proposal = store.propose("demo", candidate, "Local deterministic acceptance fixture")
        workspace = Path(self.temp.name) / "behavior-fixture"
        workspace.mkdir()
        proof = workspace / "proof.json"
        proof.write_text(json.dumps({"answer": "observed"}))
        # Operator-owned evaluator: execute a known local program, not a command
        # supplied by the candidate. Assert actual stdout and its exit status.
        def evaluate(stage):
            self.assertIn("proof.json", (stage / "SKILL.md").read_text())
            completed = subprocess.run(
                [sys.executable, "-c", "import json; print(json.load(open('proof.json'))['answer'])"],
                cwd=workspace, text=True, capture_output=True, timeout=10, check=False)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout.strip(), "observed")
            return {"passed": True, "cases": 1}
        passed = store.test(proposal["id"], evaluator=evaluate)
        self.assertTrue(passed["behavioral_passed"])
        proof.write_text(json.dumps({"answer": "wrong result"}))
        failed = store.test(proposal["id"], evaluator=evaluate)
        self.assertFalse(failed["behavioral_passed"])
        self.assertEqual(failed["behavioral_error"], "AssertionError")
        with self.assertRaisesRegex(ValueError, "behavioral"):
            store.review(proposal["id"], approve=True, reviewer="operator")

    def test_test_gate_rejects_mutated_companions_not_just_mutated_skill_markdown(self):
        forge = importlib.import_module("zeline.skillforge")
        skills = importlib.import_module("zeline.skills")
        unit = skills.PRIVATE_SKILLS_DIR / "demo"
        (unit / "scripts").mkdir(parents=True)
        (unit / "SKILL.md").write_text("# Demo\nRead fixture\n")
        (unit / "scripts" / "probe.py").write_text("print('fixture')\n")
        store = forge.ForgeStore()
        proposal = store.propose("demo", "---\nname: demo\ndescription: fixture\n---\n# Demo\n\nRead corrected fixture.\n", "fixture")
        def mutating_evaluator(stage):
            (stage / "scripts" / "probe.py").write_text("raise RuntimeError('changed')\n")
            return {"passed": True, "cases": 1}
        result = store.test(proposal["id"], evaluator=mutating_evaluator)
        self.assertFalse(result["behavioral_passed"])
        self.assertFalse(result["structural_passed"])

    def test_framework_cli_can_scan_without_calling_a_model(self):
        import contextlib
        import io
        cli = importlib.import_module("zeline.cli")
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            try:
                result = cli.main(["skillforge", "scan", "--limit", "2"])
            except SystemExit:
                self.fail("SkillForge is not reachable through the framework CLI")
        self.assertEqual(result, 0)
        import json
        self.assertIsInstance(json.loads(stream.getvalue()), list)

    def test_cli_behavioral_failure_returns_nonzero_but_inspection_is_distinct(self):
        import contextlib
        import io
        import json
        forge = importlib.import_module("zeline.skillforge")
        cli = importlib.import_module("zeline.cli")
        store = forge.ForgeStore()
        proposal = store.propose("demo", "---\nname: demo\ndescription: fixture\n---\n# Demo\n\nVerify fixture.\n", "CLI fixture")
        evaluator = Path(self.temp.name) / "evaluator.py"
        evaluator.write_text("def evaluate(stage):\n    return {'passed': False, 'cases': 1}\n")
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = cli.main(["skillforge", "test", proposal["id"],
                                  "--evaluator", str(evaluator), "--trust-evaluator"])
        self.assertFalse(json.loads(stream.getvalue())["behavioral_passed"])
        self.assertEqual(exit_code, 1)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = cli.main(["skillforge", "test", proposal["id"]])
        self.assertTrue(json.loads(stream.getvalue())["structural_passed"])
        self.assertEqual(exit_code, 0)

    def test_legacy_forge_script_uses_the_runtime_store_with_package_imports(self):
        import json
        legacy = importlib.import_module("zeline.zenith_tools.scripts.skill_forge")
        forge = importlib.import_module("zeline.skillforge")
        candidate = "---\nname: demo\ndescription: Read the local fixture\n---\n# Demo\n\nRead proof.txt.\n"
        try:
            exported = legacy.forge_proposal("missing fixture workflow", "demo", llm_fn=lambda _messages: candidate)
        except ModuleNotFoundError as exc:
            self.fail("Packaged SkillForge cannot import its proposal backend: " + str(exc))
        self.assertTrue(Path(exported).is_file())
        self.assertTrue(Path(exported).is_relative_to(Path(os.environ["ZELINE_HOME"])))
        self.assertEqual(len(forge.ForgeStore().proposals()), 1)
        with forge.ForgeStore().connect() as db:
            raw = json.dumps([dict(row) for row in db.execute("SELECT * FROM audit")])
        self.assertIn("proposed", raw)

    def test_internal_reflection_proposes_skill_edits_without_changing_active_library(self):
        import json
        forge = importlib.import_module("zeline.skillforge")
        module = importlib.import_module("zeline.agent")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        original = "# Demo\nOld procedure.\n"
        entry.write_text(original)
        agent = module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.last_turn_tool_calls = 8
        call = {"role": "assistant", "content": "", "tool_calls": [{
            "id": "revision", "type": "function", "function": {"name": "manage_skill",
            "arguments": json.dumps({"action": "patch", "name": "demo",
                                     "old_text": "Old procedure.", "new_text": "Verify the corrected procedure."})}}]}
        with mock.patch.object(agent, "_call_llm", side_effect=[call, {"content": "NO_ACTION"}]):
            summary = agent.reflect()
        self.assertEqual(entry.read_text(), original)
        proposals = forge.ForgeStore().proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "pending")
        self.assertIn("proposal", summary.lower())
        self.assertIsNone(agent.executor._skill_telemetry)

    def test_telegram_skillforge_is_owner_only_and_scan_does_not_call_model(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        if not hasattr(telegram, "_skillforge_card"):
            self.fail("SkillForge has no owner-visible Telegram command")
        card = telegram._skillforge_card()
        self.assertIn("SkillForge", card)
        self.assertIn("observ", card.lower())
        commands = {item["command"] for item in telegram._telegram_commands()}
        self.assertIn("skillforge", commands)
        self.assertIsNotNone(telegram._owner_only_reply("/skillforge", 999, ["123"]))

    def test_internal_reflection_blocks_shell_and_resets_mode_after_provider_failure(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.last_turn_tool_calls = 8
        with mock.patch.object(agent, "_call_llm", side_effect=module.ZelineError("fixture provider")):
            agent.reflect()
        self.assertFalse(agent.executor._reflection_mode)
        agent.executor._reflection_mode = True
        with mock.patch.object(agent.executor, "_dispatch") as dispatch:
            output = agent.executor.run("run_shell", {"command": "fixture must not execute"})
        dispatch.assert_not_called()
        self.assertTrue(output.startswith("ERROR"))

    def test_internal_reflection_honors_operator_plugins_before_proposing(self):
        forge = importlib.import_module("zeline.skillforge")
        module = importlib.import_module("zeline.agent")
        plugins = importlib.import_module("zeline.plugins")
        agent = module.Zeline(identity="telegram:fixture", tool_profile="full")
        args = {"action": "create", "name": "demo", "content": "# Demo\nRead fixture.\n"}
        agent.executor._reflection_mode = True
        plugin = mock.Mock()
        plugin.before.return_value = plugins.HookOutcome(args=args, denied_by="owner-policy", reason="Fixture deny")
        agent.executor.plugins = plugin
        result = agent.executor.run("manage_skill", args)
        self.assertIn("owner-policy", result)
        plugin.before.assert_called_once_with("manage_skill", args)
        plugin.after.assert_not_called()
        self.assertEqual(forge.ForgeStore().proposals(), [])
        plugin.before.return_value = plugins.HookOutcome(args={})
        plugin.after.return_value = "redacted fixture inventory"
        result = agent.executor.run("list_memory", {})
        self.assertEqual(result, "redacted fixture inventory")
        plugin.after.assert_called_once()

    def test_reflection_proposal_store_failure_is_an_error_not_a_broken_turn(self):
        import sqlite3
        forge = importlib.import_module("zeline.skillforge")
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.executor._reflection_mode = True
        with mock.patch.object(forge, "ForgeStore", side_effect=sqlite3.OperationalError("fixture unavailable")):
            output = agent.executor.run("manage_skill", {"action": "create", "name": "demo", "content": "# Demo\nRead fixture.\n"})
        self.assertTrue(output.startswith("ERROR"))
        self.assertNotIn("fixture unavailable", output)

    def test_same_round_load_has_no_attribution_until_model_can_read_the_skill(self):
        forge = importlib.import_module("zeline.skillforge")
        skills = importlib.import_module("zeline.skills")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo.md"
        entry.write_text("# Demo\nRead proof.txt.\n")
        observer = forge.TurnTelemetry("fixture")
        if not hasattr(observer, "begin_round"):
            self.fail("Parallel skill loads can attribute results before the model reads the skill")
        observer.begin_round()
        observer.observe("load_skill", {"name": "demo"}, entry.read_text(), include_private=True)
        observer.observe("read_file", {"path": "proof.txt"}, "fixture", include_private=True)
        observer.finish("completed")
        score = forge.ForgeStore().score("demo", revision=forge.revision_of(entry.read_text()))
        self.assertEqual(score["samples"], 0)
        self.assertEqual(score["unknown"], 1)

    def test_usage_store_keeps_concurrent_observations_and_applies_recency_decay(self):
        from concurrent.futures import ThreadPoolExecutor
        forge = importlib.import_module("zeline.skillforge")
        store = forge.ForgeStore()
        now = 20_000_000.0
        def record(index):
            store.record_usage("fixture", str(index), "demo", "private", "a" * 64,
                               "success", tool_successes=1, now=now)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, range(16)))
        recent = store.score("demo", revision="a" * 64, now=now)
        older = store.score("demo", revision="a" * 64, now=now + 30 * 86400)
        self.assertEqual(recent["samples"], 16)
        self.assertAlmostEqual(older["effective_samples"], recent["effective_samples"] / 2)
        self.assertGreater(recent["effectiveness"], older["effectiveness"])


if __name__ == "__main__":
    unittest.main()
