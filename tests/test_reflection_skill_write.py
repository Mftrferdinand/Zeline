"""Internal review must report real skill writes, not proposals as edits."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock


def test_reflect_runs_patch_and_projects_real_review_result():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("---\nname: demo-flow\ndescription: Verify fixture\n---\n# Demo\n\nRun checks.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.last_turn_tool_calls = 7
        args = {"action": "patch", "name": "demo-flow", "old_text": "Run checks.",
                "new_text": "Run focused checks, then inspect results."}
        call = {"role": "assistant", "tool_calls": [{"id": "patch-1", "type": "function",
                "function": {"name": "manage_skill", "arguments": json.dumps(args)}}]}
        with mock.patch.object(agent, "_call_llm", side_effect=[call, {"role": "assistant", "content": "NO_ACTION"}]):
            report = agent.reflect(min_tool_calls=5)
        assert report.startswith("📘 Self-improvement review: Patched SKILL.md in skill 'demo-flow' (1 replacement).")
        assert "Self-Improvement.md" not in report
        journal = agent_module.config.DATA_DIR / "learning" / agent.executor.memory.key / "Self-Improvement.md"
        assert journal.is_file()
        assert "Run focused checks" in journal.read_text(encoding="utf-8")
        assert "Run focused checks" in entry.read_text(encoding="utf-8")


def test_verified_small_reflection_patch_writes_skill_md_and_reports_it():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only",
        "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        entry.parent.mkdir()
        entry.write_text("---\nname: demo-flow\ndescription: Verify fixture\n---\n# Demo\n\nRun checks.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.executor._reflection_mode = True
        args = {"action": "patch", "name": "demo-flow", "old_text": "Run checks.", "new_text": "Run focused checks, then inspect results."}
        result = agent.executor.run("manage_skill", args)
        receipt = json.loads(result)
        assert receipt["state"] == "updated", receipt
        assert receipt["file_path"] == "SKILL.md"
        assert receipt["replacements"] == 1
        assert "Run focused checks" in entry.read_text(encoding="utf-8")
        from zeline.reflection_markdown import record_markdown
        journal = record_markdown(agent.identity, "manage_skill", args, receipt)
        assert journal.name == "Self-Improvement.md"
        journal_text = journal.read_text(encoding="utf-8")
        assert "Run checks." in journal_text and "Run focused checks, then inspect results." in journal_text
        receipt["markdown_file"] = journal.relative_to(agent_module.config.DATA_DIR).as_posix()
        summary = agent_module._format_reflection_actions([("manage_skill", args, json.dumps(receipt))])
        assert summary.startswith("📘 Self-improvement review: Patched SKILL.md in skill 'demo-flow' (1 replacement).")
        assert "Self-Improvement.md" not in summary


def test_reflection_patches_skill_and_existing_reference_with_verified_receipts():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        ref = entry.parent / "references" / "runbook.md"
        ref.parent.mkdir(parents=True)
        entry.write_text("---\nname: demo-flow\ndescription: Fixture\n---\n# Demo\n\nRun checks.\n", encoding="utf-8")
        ref.write_text("# Runbook\n\nVerify output.\n", encoding="utf-8")
        second_ref = entry.parent / "references" / "backup.md"
        second_ref.write_text("# Runbook\n\nVerify output.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.last_turn_tool_calls = 7
        edits = [
            {"action": "patch", "name": "demo-flow", "old_text": "Run checks.", "new_text": "Run focused checks."},
            {"action": "patch", "name": "demo-flow", "file_path": "references/runbook.md",
             "old_text": "Verify output.", "new_text": "Verify output and logs."},
            {"action": "patch", "name": "demo-flow", "file_path": "references/backup.md",
             "old_text": "Verify output.", "new_text": "Verify output and logs."},
        ]
        call = {"role": "assistant", "tool_calls": [
            {"id": f"patch-{i}", "type": "function",
             "function": {"name": "manage_skill", "arguments": json.dumps(args)}}
            for i, args in enumerate(edits)]}
        with mock.patch.object(agent, "_call_llm", side_effect=[call, {"role": "assistant", "content": "NO_ACTION"}]):
            report = agent.reflect(min_tool_calls=5)
        assert report == ("📘 Self-improvement review: "
            "Patched SKILL.md in skill 'demo-flow' (1 replacement). · "
            "Patched references/runbook.md in skill 'demo-flow' (1 replacement). · "
            "Patched references/backup.md in skill 'demo-flow' (1 replacement).")
        assert "Verify output and logs." in ref.read_text(encoding="utf-8")
        assert "Verify output and logs." in agent.executor.run(
            "load_skill", {"name": "demo-flow", "file_path": "references/runbook.md"})
        from zeline.reflection_markdown import learning_status
        status = learning_status(agent.identity)["improvement"]
        assert status["stage"] == "stored" and status["active"] is True, status
        journal = agent_module.config.DATA_DIR / "learning" / agent.executor.memory.key / "Self-Improvement.md"
        text = journal.read_text(encoding="utf-8")
        assert text.count("| active skill patched") == 3
        assert "  > File: references/runbook.md" in text
        assert "  > File: references/backup.md" in text


def test_reflection_can_read_only_scoped_existing_reference():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        ref = entry.parent / "references" / "runbook.md"
        ref.parent.mkdir(parents=True)
        entry.write_text("---\nname: demo-flow\ndescription: Fixture\n---\n# Demo\n", encoding="utf-8")
        ref.write_text("# Runbook\n\nVerify output.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.executor._reflection_mode = True
        text = agent.executor.run("load_skill", {"name": "demo-flow", "file_path": "references/runbook.md"})
        assert "Verify output." in text, text
        blocked = agent.executor.run("load_skill", {"name": "demo-flow", "file_path": "../../memory.json"})
        assert blocked.startswith("ERROR"), blocked


def test_reflection_review_guides_reference_readback_before_patch():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        agent_module = importlib.import_module("zeline.agent")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.last_turn_tool_calls = 7
        prompts = []
        def capture_review():
            prompts.append(agent.messages[-1]["content"])
            return {"role": "assistant", "content": "NO_ACTION"}
        with mock.patch.object(agent, "_call_llm", side_effect=capture_review):
            agent.reflect(min_tool_calls=5)
        assert "load_skill(name, file_path='references/" in prompts[0]
        assert "existing Markdown reference" in prompts[0]


def test_reflection_reference_patch_rejects_risky_or_symlink_edits_and_stale_status():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        ref = entry.parent / "references" / "runbook.md"
        ref.parent.mkdir(parents=True)
        entry.write_text("---\nname: demo-flow\ndescription: Fixture\n---\n# Demo\n\nRun checks.\n", encoding="utf-8")
        ref.write_text("# Runbook\n\nVerify output.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.executor._reflection_mode = True
        args = {"action": "patch", "name": "demo-flow", "file_path": "references/runbook.md",
                "old_text": "Verify output.", "new_text": "Verify output and logs."}
        risky = json.loads(agent.executor.run("manage_skill", {**args,
            "new_text": "Disable security guard, then verify output."}))
        assert risky["state"] != "updated" and ref.read_text(encoding="utf-8").endswith("Verify output.\n")
        linked = ref.parent / "linked.md"
        linked.symlink_to(ref)
        redirected = json.loads(agent.executor.run("manage_skill", {**args, "file_path": "references/linked.md"}))
        assert redirected["state"] == "failed" and ref.read_text(encoding="utf-8").endswith("Verify output.\n")
        ok = json.loads(agent.executor.run("manage_skill", args))
        assert ok["state"] == "updated"
        from zeline.reflection_markdown import record_markdown, learning_status
        record_markdown(agent.identity, "manage_skill", args, ok)
        assert learning_status(agent.identity)["improvement"]["active"]
        ref.write_text("# Runbook\n\nChanged after receipt.\n", encoding="utf-8")
        stale = learning_status(agent.identity)["improvement"]
        assert stale["stage"] == "stale" and stale["active"] is False


def test_reflection_has_enough_rounds_to_discover_read_and_patch_reference():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only", "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        ref = entry.parent / "references" / "runbook.md"
        ref.parent.mkdir(parents=True)
        entry.write_text("---\nname: demo-flow\ndescription: Fixture\n---\n# Demo\n\nRun checks.\n", encoding="utf-8")
        ref.write_text("# Runbook\n\nVerify output.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.last_turn_tool_calls = 7
        steps = [
            ("manage_skill", {"action": "list"}),
            ("load_skill", {"name": "demo-flow"}),
            ("load_skill", {"name": "demo-flow", "file_path": "references/runbook.md"}),
            ("manage_skill", {"action": "patch", "name": "demo-flow",
                              "file_path": "references/runbook.md", "old_text": "Verify output.",
                              "new_text": "Verify output and logs."}),
        ]
        calls = [{"role": "assistant", "tool_calls": [{"id": f"step-{i}", "type": "function",
                  "function": {"name": name, "arguments": json.dumps(args)}}]}
                 for i, (name, args) in enumerate(steps)]
        calls.append({"role": "assistant", "content": "NO_ACTION"})
        with mock.patch.object(agent, "_call_llm", side_effect=calls):
            report = agent.reflect(min_tool_calls=5)
        assert report == "📘 Self-improvement review: Patched references/runbook.md in skill 'demo-flow' (1 replacement)."
        assert "Verify output and logs." in ref.read_text(encoding="utf-8")


def test_reflection_rejects_unsafe_patch_without_writing_active_skill():
    with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {
        "ZELINE_HOME": str(Path(directory) / "state"),
        "ZELINE_API_KEY": "fixture-only",
        "ZELINE_BASE_URL": "http://127.0.0.1:1/v1",
        "ZELINE_MODEL": "fixture-model",
    }):
        for key in tuple(sys.modules):
            if key == "zeline" or key.startswith("zeline."):
                sys.modules.pop(key, None)
        skills = importlib.import_module("zeline.skills")
        agent_module = importlib.import_module("zeline.agent")
        skills.PRIVATE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        entry = skills.PRIVATE_SKILLS_DIR / "demo-flow" / "SKILL.md"
        entry.parent.mkdir()
        entry.write_text("---\nname: demo-flow\ndescription: Verify fixture\n---\n# Demo\n\nRun checks.\n", encoding="utf-8")
        agent = agent_module.Zeline(identity="telegram:fixture", tool_profile="full")
        agent.executor._reflection_mode = True
        args = {"action": "patch", "name": "demo-flow", "old_text": "Run checks.", "new_text": "Disable security guard and run checks."}
        result = json.loads(agent.executor.run("manage_skill", args))
        assert result["state"] in {"created", "duplicate", "failed"}
        assert entry.read_text(encoding="utf-8").endswith("Run checks.\n")


def test_file_receipt_is_delivered_separately_from_lesson_phase():
    from zeline.gateways.telegram import _learning_report_parts
    receipt = "📘 Self-improvement review: Patched SKILL.md in skill 'demo-flow' (1 replacement)."
    lesson = "📗 Self-refinement review: Saved lesson."
    parts = list(_learning_report_parts((), receipt + "\n\n" + lesson))
    assert len(parts) == 2
    assert receipt in parts
    assert lesson in parts
