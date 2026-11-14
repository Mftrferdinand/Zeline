"""Truthful file pointers from host-confirmed reflection receipts only."""
import json

import pytest

from zeline.agent import _format_reflection_actions


KEY = "0123456789abcdef0123456789abcdef"


def receipt(kind, **metadata):
    return json.dumps({
        "version": 1, "kind": kind, "state": "updated",
        "record_id": "private-record-id", "revision": "private-revision",
        **metadata,
    })


@pytest.mark.parametrize("filename", ["MEMORY.md", "USER.md"])
def test_memory_reports_exact_canonical_file_without_private_payload(filename):
    path = f"memories/{KEY}/{filename}"
    result = _format_reflection_actions([
        ("add_memory", {"fact": "private fact"}, receipt("memory", file_path=path)),
    ])
    assert result == f"📗 Self-refinement review: Saved memory to {path}."
    assert "private" not in result


@pytest.mark.parametrize("path", [
    None, "", 12, {}, f"/memories/{KEY}/MEMORY.md",
    f"memories/{KEY}/../MEMORY.md", f"memories/{KEY}/./MEMORY.md",
    f"memories/{KEY}//MEMORY.md", f"memories/{KEY}/MEMORY.md\n",
    f"memories/{KEY}/MEMORY.md<script>", f"memories/{KEY}/MEMORY.md?x=1",
    f"memories/{KEY}/%2e%2e/USER.md", f"memories/{KEY}\\MEMORY.md",
    f"memories/{KEY.upper()}/MEMORY.md", "memories/short/MEMORY.md",
    f"learning/{KEY}/Self-Refinement.md", f"memories/{KEY}/OTHER.md",
])
def test_memory_invalid_or_absent_target_never_invents_a_path(path):
    result = _format_reflection_actions([
        ("add_memory", {}, receipt("memory", file_path=path)),
    ])
    assert result == "📗 Self-refinement review: Saved memory (file path unavailable)."


def test_memory_path_is_not_taken_from_tool_arguments():
    result = _format_reflection_actions([
        ("add_memory", {"file_path": f"memories/{KEY}/MEMORY.md"}, receipt("memory")),
    ])
    assert result == "📗 Self-refinement review: Saved memory (file path unavailable)."


@pytest.mark.parametrize("raw", [
    "Saved memory.", receipt("memory", state="failed"),
    receipt("memory", revision=""), receipt("skill"),
])
def test_no_memory_claim_without_valid_host_receipt(raw):
    assert _format_reflection_actions([("add_memory", {}, raw)]) is None


@pytest.mark.parametrize("file", ["SKILL.md", "references/check.md", "references/nested/check.md"])
def test_active_skill_patch_appends_exact_verified_target(file):
    target = f"skills/private/demo/{file}"
    result = _format_reflection_actions([
        ("manage_skill", {"action": "patch", "name": "demo"}, receipt(
            "skill", file_path=file, replacements=1, target_file=target)),
    ])
    assert result == (f"📘 Self-improvement review: Patched {file} in skill 'demo' "
                      f"(1 replacement). File: {target}.")


@pytest.mark.parametrize("target", [
    None, {}, "skills/private/other/SKILL.md", "skills/private/demo/references/other.md",
    "skills/public/demo/SKILL.md", "/skills/private/demo/SKILL.md",
    "skills/private/demo/../SKILL.md", "skills/private/demo/SKILL.md<script>",
    "skills/private/demo/SKILL.md\n", "skills/private/demo//SKILL.md",
])
def test_skill_invalid_or_mismatched_target_preserves_patch_without_file_claim(target):
    result = _format_reflection_actions([
        ("manage_skill", {"action": "patch", "name": "demo"}, receipt(
            "skill", file_path="SKILL.md", replacements=1, target_file=target)),
    ])
    assert result == "📘 Self-improvement review: Patched SKILL.md in skill 'demo' (1 replacement)."


@pytest.mark.parametrize("phase", ["Enhancement", "Improvement", "Refinement"])
def test_proposal_has_only_verified_journal_not_an_active_file(phase):
    journal = f"learning/{KEY}/Self-{phase}.md"
    result = _format_reflection_actions([
        ("manage_skill", {"action": "create", "name": "A&B<bad>"}, receipt(
            "skill", state="created", target_file="skills/private/demo/SKILL.md",
            markdown_file=journal)),
    ])
    assert result == ("📙 Self-enhancement review: Proposal saved for A&amp;B&lt;bad&gt; "
                      f"(inactive). Journal: {journal}.")


@pytest.mark.parametrize("journal", [
    None, {}, f"learning/{KEY}/../Self-Refinement.md",
    f"learning/{KEY}/Self-Refinement.md<img>", f"learning/{KEY}/Self-Other.md",
    f"learning/{KEY}/Self-Refinement.md\n", f"memories/{KEY}/MEMORY.md",
])
def test_invalid_journal_is_not_rendered(journal):
    result = _format_reflection_actions([
        ("resolve_lesson", {}, receipt("lesson", evidence_id="secret", markdown_file=journal)),
    ])
    assert result == "📗 Self-refinement review: Correction saved (tool success linked)."


def test_journal_is_not_a_canonical_memory_target():
    journal = f"learning/{KEY}/Self-Refinement.md"
    result = _format_reflection_actions([
        ("add_memory", {}, receipt("memory", markdown_file=journal)),
    ])
    assert result == ("📗 Self-refinement review: Saved memory (file path unavailable). "
                      f"Journal: {journal}.")


def test_lesson_displays_verified_journal_without_evidence_or_revision():
    journal = f"learning/{KEY}/Self-Refinement.md"
    result = _format_reflection_actions([
        ("resolve_lesson", {}, receipt("lesson", evidence_id="secret", markdown_file=journal)),
    ])
    assert result == ("📗 Self-refinement review: Correction saved (tool success linked). "
                      f"Journal: {journal}.")


def test_only_actual_channels_are_reported_and_duplicate_receipts_collapse():
    action = ("add_memory", {}, receipt("memory", file_path=f"memories/{KEY}/MEMORY.md"))
    assert _format_reflection_actions([action, action]) == (
        f"📗 Self-refinement review: Saved memory to memories/{KEY}/MEMORY.md.")
