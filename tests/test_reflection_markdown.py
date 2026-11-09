"""Reflection's three channels must leave real, identity-scoped Markdown evidence."""
import json
from pathlib import Path

from zeline import config
from zeline.agent import _format_reflection_actions
from zeline.reflection_markdown import record_markdown, review_context, refinement_context


def _receipt(kind, summary, record_id="r1"):
    return {"version": 1, "kind": kind, "state": "created", "record_id": record_id,
            "revision": 1, "summary": summary}


def test_three_self_reports_describe_distinct_verified_operations():
    patch = {**_receipt("skill", "Verified file write", "patch-1"),
             "state": "updated", "file_path": "SKILL.md", "replacements": 1,
             "markdown_file": "learning/id/Self-Improvement.md"}
    created = {**_receipt("skill", "Proposed browser triage", "proposal-1"),
               "markdown_file": "learning/id/Self-Enhancement.md"}
    memory = {**_receipt("memory", "User prefers short replies", "memory-1"),
              "markdown_file": "learning/id/Self-Refinement.md"}
    report = _format_reflection_actions([
        ("manage_skill", {"action": "patch", "name": "zeline-development"}, json.dumps(patch)),
        ("manage_skill", {"action": "create", "name": "browser-triage"}, json.dumps(created)),
        ("add_memory", {"fact": "User prefers short replies"}, json.dumps(memory)),
    ])
    parts = report.split("\n\n")
    assert len(parts) == 3
    assert parts[0] == "📙 Self-enhancement review: Proposal saved for browser-triage (inactive)."
    assert parts[1] == "📘 Self-improvement review: Patched SKILL.md in skill 'zeline-development' (1 replacement)."
    assert parts[2] == "📗 Self-refinement review: Memory saved."
    assert "learning/" not in report and "💾" not in report


def test_improvement_file_contains_applied_change_not_generic_next_template(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    args = {"action": "patch", "name": "triage", "old_text": "Run checks.",
            "new_text": "Run focused checks, then inspect results."}
    receipt = {**_receipt("skill", "Verified file write", "patch-1"),
               "state": "updated", "file_path": "SKILL.md", "replacements": 1}
    path = record_markdown("cli:local", "manage_skill", args, receipt)
    content = path.read_text(encoding="utf-8")
    assert "Run checks." in content and "Run focused checks, then inspect results." in content
    assert "active skill patched" in content and "patch-1@1" in content
    assert "Next:" not in content


def test_refinement_file_separates_memory_from_evidenced_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    receipt = {**_receipt("lesson", "Retry web lookup with alternate endpoint", "lesson-1"),
               "evidence_id": "success:lookup-1"}
    path = record_markdown("cli:local", "resolve_lesson", {"tool": "web_search"}, receipt)
    content = path.read_text(encoding="utf-8")
    assert "Retry web lookup" in content and "success:lookup-1" in content
    assert "web_search" in content and "Next:" not in content


def test_three_self_channels_persist_distinct_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cases = [
        ("manage_skill", {"action": "create", "name": "new-workflow"}, _receipt("skill", "New reusable workflow"), "Self-Enhancement.md"),
        ("manage_skill", {"action": "patch", "name": "old-workflow"}, _receipt("skill", "Replace stale step"), "Self-Improvement.md"),
        ("add_memory", {"fact": "User prefers short replies"}, _receipt("memory", "User prefers short replies"), "Self-Refinement.md"),
    ]
    for name, args, receipt, filename in cases:
        path = record_markdown("telegram:alice", name, args, receipt)
        assert path.name == filename
        assert path.read_text(encoding="utf-8").startswith("# Self-")
        assert receipt["summary"] in path.read_text(encoding="utf-8")
        assert path.parent.parent == tmp_path / "learning"
        assert record_markdown("telegram:alice", name, args, receipt) == path  # idempotent
        assert path.read_text(encoding="utf-8").count("Record: r1@1") == 1
        other = record_markdown("telegram:bob", name, args, receipt)
        assert other.parent != path.parent


def test_refinement_chat_is_compact_while_journal_keeps_full_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    fact = "Never use obsolete mail config; inspect the installed version before changing settings."
    receipt = {**_receipt("memory", fact), "record_id": "mem_receipt", "summary": fact}
    path = record_markdown("telegram:owner", "add_memory", {"fact": fact}, receipt)
    receipt["markdown_file"] = path.relative_to(tmp_path).as_posix()
    report = _format_reflection_actions([("add_memory", {"fact": fact}, json.dumps(receipt))])
    assert report == "📗 Self-refinement review: Memory saved."
    assert fact in path.read_text(encoding="utf-8")
    assert "mem_receipt@1" in path.read_text(encoding="utf-8")


def test_other_reflection_channels_keep_chat_compact_without_losing_action_type():
    from zeline.agent import _format_reflection_actions
    proposal = {"version": 1, "kind": "skill", "state": "created", "record_id": "forge_fixture",
                "revision": 1, "summary": "A very long proposal that belongs in the journal only.",
                "markdown_file": "learning/owner/Self-Enhancement.md"}
    correction = {"version": 1, "kind": "lesson", "state": "updated", "record_id": "lesson_fixture",
                  "revision": 1, "summary": "Correct a long tool failure detail in the journal.",
                  "evidence_id": "success:fixture", "markdown_file": "learning/owner/Self-Refinement.md"}
    result = _format_reflection_actions([
        ("manage_skill", {"action": "create", "name": "triage"}, json.dumps(proposal)),
        ("resolve_lesson", {"tool": "web_search"}, json.dumps(correction)),
    ])
    assert result == ("📙 Self-enhancement review: Proposal saved for triage (inactive).\n\n"
                      "📗 Self-refinement review: Correction saved (tool success linked).")
    assert "fixture" not in result and "learning/" not in result


def test_receipt_only_claims_markdown_when_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    receipt = _receipt("memory", "A stable correction")
    path = record_markdown("cli:local", "add_memory", {}, receipt)
    receipt["markdown_file"] = path.relative_to(tmp_path).as_posix()
    report = _format_reflection_actions([("add_memory", {}, json.dumps(receipt))])
    assert report == "📗 Self-refinement review: Memory saved."
    assert path.name not in report and "learning/" not in report
    assert receipt["summary"] in path.read_text(encoding="utf-8")
    assert path.exists()


def test_multiple_records_are_separate_and_revisions_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    first = _receipt("memory", "First fact")
    path = record_markdown("cli:local", "add_memory", {}, first)
    second = {**first, "record_id": "r2", "summary": "Second fact"}
    record_markdown("cli:local", "add_memory", {}, second)
    revision = {**first, "revision": 10, "summary": "Updated fact"}
    record_markdown("cli:local", "add_memory", {}, revision)
    text = path.read_text(encoding="utf-8")
    assert text.count("- Record:") == 3
    assert "First fact" in text and "- Record: r2@1" in text
    assert "Second fact" in text and "- Record: r1@10" in text
    assert "Updated fact" in text


def test_next_review_reads_improvement_outcomes_not_as_proven_benefit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    record_markdown("cli:local", "manage_skill",
                    {"action": "patch", "name": "triage"},
                    {**_receipt("skill", "Patched triage"), "state": "updated",
                     "file_path": "SKILL.md", "record_id": "patch-1"})
    context = review_context("cli:local")
    assert "Self-Improvement.md" in context
    assert "triage" in context and "patch-1@1" in context
    assert "not proof" in context.lower()


def test_refinement_readback_tracks_only_current_recalled_record(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    record_markdown("cli:local", "add_memory", {}, _receipt("memory", "Prefer compact UI"))
    live = [{"id": "r1", "revision": 1, "text": "Prefer compact UI"}]
    assert "Self-Refinement.md" in refinement_context("cli:local", live)
    assert "r1@1" in refinement_context("cli:local", live)
    assert refinement_context("cli:other", live) == ""
    assert refinement_context("cli:local", [{**live[0], "revision": 2}]) == ""
    assert refinement_context("cli:local", [{**live[0], "text": "Corrected UI"}]) == ""
    assert refinement_context("cli:local", []) == ""


def test_refinement_readback_recognizes_actual_long_memory_receipt(tmp_path, monkeypatch):
    from zeline import memory
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    fact = "Prefer a hyphenated journal filename. " + "long-term convention " * 12
    store = memory.MemoryStore("cli:local")
    receipt = store.add_receipt(fact)
    assert len(fact) > 180 and len(receipt["summary"]) == 180
    assert record_markdown("cli:local", "add_memory", {}, receipt)
    live = store.records()
    assert "Self-Refinement.md" in refinement_context("cli:local", live)
    assert refinement_context("cli:local", [{**live[0], "revision": 2}]) == ""


def test_next_review_reads_prior_skill_proposals_without_promoting_them(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    record_markdown("cli:local", "manage_skill",
                    {"action": "create", "name": "triage", "content": "Verify the failed tool."},
                    _receipt("skill", "Triage a flaky tool"))
    context = review_context("cli:local")
    assert "triage" in context and "r1@1" in context
    assert "proposal, not active" in context
    assert "review" in context.lower()
    assert "triage" not in review_context("cli:other")


def test_journal_records_distinct_next_action_for_each_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    proposal = record_markdown("cli:local", "manage_skill",
        {"action": "create", "name": "triage", "content": "Run tests before changing production."},
        _receipt("skill", "Reusable triage procedure"))
    patch = record_markdown("cli:local", "manage_skill",
        {"action": "patch", "name": "triage", "old_text": "bad", "new_text": "good"},
        {**_receipt("skill", "Patched triage"), "state": "updated",
         "file_path": "SKILL.md", "replacements": 1, "record_id": "r2"})
    correction = record_markdown("cli:local", "resolve_lesson", {},
        {**_receipt("lesson", "Retry succeeded"), "record_id": "r3",
         "evidence_id": "success:e1"})
    assert "triage" in proposal.read_text() and "Run tests before changing production." in proposal.read_text()
    assert "review" in proposal.read_text().lower() and "not active" in proposal.read_text()
    assert "triage" in patch.read_text() and "SKILL.md" in patch.read_text()
    assert "success:e1" in correction.read_text()


def test_markdown_redacts_configured_provider_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "API_KEY", "private-provider-token-123")
    path = record_markdown("cli:local", "add_memory", {},
                           _receipt("memory", "Never print private-provider-token-123"))
    text = path.read_text(encoding="utf-8")
    assert "private-provider-token-123" not in text
    assert "[REDACTED]" in text


def test_new_skill_proposal_markdown_contains_procedure_not_only_label(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = record_markdown("cli:local", "manage_skill",
                           {"action": "create", "name": "workflow", "content": "Run checks before deploy."},
                           _receipt("skill", "Proposed workflow"))
    assert "Run checks before deploy." in path.read_text(encoding="utf-8")
    assert "proposal, not active" in path.read_text(encoding="utf-8")


def test_failed_markdown_write_cannot_be_reported_as_a_saved_file(tmp_path, monkeypatch):
    from zeline import reflection_markdown
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    def fail_replace(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(reflection_markdown.os, "replace", fail_replace)
    import pytest
    with pytest.raises(OSError, match="disk full"):
        record_markdown("cli:local", "add_memory", {}, _receipt("memory", "Fact"))
    assert not list((tmp_path / "learning").rglob("*.md"))


def test_unrecognized_and_unverified_events_do_not_create_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert record_markdown("cli:local", "manage_skill", {"action": "list"}, _receipt("skill", "Listed")) is None
    assert record_markdown("cli:local", "manage_skill", {"action": "create"}, {**_receipt("skill", "Nope"), "state": "duplicate"}) is None
    assert not (tmp_path / "learning").exists()
