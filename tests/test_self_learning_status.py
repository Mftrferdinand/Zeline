"""Read-only closed-loop status must not upgrade observations into task proof."""
from types import SimpleNamespace

import pytest


@pytest.fixture
def stores(tmp_path, monkeypatch):
    from zeline import config, memory, skills
    from zeline.skillforge import ForgeStore
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(skills, "PRIVATE_SKILLS_DIR", tmp_path / "skills")
    return SimpleNamespace(root=tmp_path, forge=ForgeStore(), memory=memory.MemoryStore("telegram:owner"), skills=skills)


def test_status_does_not_create_canonical_stores_for_untrusted_journals(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    record_markdown(identity, "add_memory", {"fact": "Fake"}, {"version": 1, "kind": "memory",
                    "state": "created", "record_id": "fake", "revision": 1, "summary": "Fake"})
    record_markdown(identity, "manage_skill", {"action": "create", "name": "fake"},
                    {"version": 1, "kind": "skill", "state": "created", "record_id": "forge_fake",
                     "revision": "a" * 64, "summary": "Fake"})
    assert learning_status(identity)["refinement"]["stage"] == "unverified"
    assert learning_status(identity)["enhancement"]["stage"] == "unverified"
    assert not (stores.root / "memory" / "memory.sqlite3").exists()
    # The fixture owns a forge DB; no new memory database may be materialized.


def test_normal_turn_memory_write_is_visible_as_stored_not_verified(stores):
    from zeline.tools import ToolExecutor
    from zeline.reflection_markdown import learning_status

    executor = ToolExecutor("telegram:owner", profile="full")
    fact = "Owner prefers hyphenated journal names."
    assert executor.run("add_memory", {"fact": fact}).startswith("OK, saved.")
    status = learning_status("telegram:owner")["refinement"]
    assert status["stage"] == "stored" and status["verified"] == 0
    assert executor.run("add_memory", {"fact": fact}) == "That fact is already in memory."
    assert learning_status("telegram:owner")["refinement"] == status
    assert learning_status("telegram:outsider")["refinement"]["stage"] == "empty"


def test_journal_import_failure_never_invalidates_committed_memory(stores, monkeypatch):
    from zeline.tools import ToolExecutor
    from zeline import reflection_markdown
    from zeline.reflection_markdown import learning_status
    executor = ToolExecutor("telegram:owner", profile="full")
    def missing_lock(*args, **kwargs):
        raise ImportError('platform lock unavailable')
    monkeypatch.setattr(reflection_markdown, 'record_markdown', missing_lock)
    result = executor.run("add_memory", {"fact": "Owner uses hyphenated journal names."})
    assert result.startswith('OK, saved.') and 'journal unavailable' in result
    assert any('hyphenated journal' in item['text'] for item in stores.memory.records())
    assert learning_status("telegram:owner")["refinement"]["stage"] == "empty"


def test_batch_memory_correction_projects_only_live_changed_revision(stores):
    from zeline.tools import ToolExecutor
    from zeline.reflection_markdown import learning_status
    executor = ToolExecutor("telegram:owner", profile="full")
    receipt = stores.memory.add_receipt("Old format is underscores.")
    update = executor.run("add_memory", {"operations": [
        {"action": "replace", "id": receipt["record_id"], "expected_revision": 1,
         "text": "Use hyphenated journal names."}]})
    assert '"success": true' in update
    status = learning_status("telegram:owner")["refinement"]
    assert status["stage"] == "stored" and status["verified"] == 0
    assert status["active"] is True
    journal = (stores.root / "learning" / stores.memory.key / "Self-Refinement.md").read_text()
    assert "Use hyphenated journal names." in journal
    assert f"{receipt['record_id']}@2" in journal
    assert learning_status("telegram:other")["refinement"]["stage"] == "empty"


def test_aggregate_keeps_earlier_active_memory_when_latest_is_stale(stores):
    import json
    import sqlite3
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    first = stores.memory.add_receipt("Keep this active fact.")
    second = stores.memory.add_receipt("This fact will be replaced.")
    for receipt in (first, second):
        record_markdown(identity, "add_memory", {"fact": receipt["summary"]}, receipt)
    db_path = stores.root / "memory" / "memory.sqlite3"
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT payload FROM memory_items WHERE id=?", (second["record_id"],)).fetchone()
        item = json.loads(row[0])
        item["revision"] = 2
        db.execute("UPDATE memory_items SET payload=? WHERE id=?", (json.dumps(item), second["record_id"]))
    status = learning_status(identity)["refinement"]
    assert status["active_records"] == 1
    assert status["active_observations"] == status["active_verified"] == 0
    assert status["stage"] == "stored" and status["active"] is False


def test_aggregate_deduplicates_journals_and_scopes_evidence(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    receipt = stores.memory.add_receipt("Keep scoped evidence.")
    record_markdown(identity, "add_memory", {"fact": receipt["summary"]}, receipt)
    stores.memory.record_retrieval("owner-turn", stores.memory.recall("scoped evidence")["records"])
    path = stores.root / "learning" / stores.memory.key / "Self-Refinement.md"
    entry = path.read_text().split("- Record: ", 1)[1]
    path.write_text(path.read_text() + "- Record: " + entry)
    owner = learning_status(identity)["refinement"]
    assert owner["active_records"] == 1
    assert owner["active_observations"] == 1
    assert owner["active_verified"] == 0
    assert learning_status("telegram:other")["refinement"]["active_records"] == 0


def test_aggregate_rejects_mixed_identity_journal_pointers(stores):
    from zeline import memory
    from zeline.reflection_markdown import learning_status, record_markdown
    owner = "telegram:owner"
    other = "telegram:other"
    valid = stores.memory.add_receipt("Owner canonical fact.")
    record_markdown(owner, "add_memory", {"fact": valid["summary"]}, valid)
    foreign = memory.MemoryStore(other)
    outsider = foreign.add_receipt("Foreign canonical fact.")
    record_markdown(other, "add_memory", {"fact": outsider["summary"]}, outsider)
    source = stores.root / "learning" / foreign.key / "Self-Refinement.md"
    target = stores.root / "learning" / stores.memory.key / "Self-Refinement.md"
    target.write_text(target.read_text() + "- Record: " + source.read_text().split("- Record: ", 1)[1])
    status = learning_status(owner)["refinement"]
    assert status["active_records"] == 1
    assert status["active"] is False


def test_aggregate_malformed_journal_does_not_claim_active(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    receipt = stores.memory.add_receipt("Valid canonical memory.")
    record_markdown("telegram:owner", "add_memory", {"fact": receipt["summary"]}, receipt)
    path = stores.root / "learning" / stores.memory.key / "Self-Refinement.md"
    path.write_text(path.read_text() + "- Record: broken@1 | stored\n  > corrupt")
    status = learning_status("telegram:owner")["refinement"]
    assert status["active_records"] == 0
    assert status["stage"] == "empty"


def test_aggregate_bad_canonical_assessment_fails_closed(stores):
    import sqlite3
    from zeline.reflection_markdown import learning_status, record_markdown
    receipt = stores.memory.add_receipt("Malformed comparison evidence.")
    record_markdown("telegram:owner", "add_memory", {"fact": receipt["summary"]}, receipt)
    stores.memory.record_retrieval("broken", stores.memory.recall("Malformed comparison")["records"])
    with sqlite3.connect(stores.root / "memory" / "memory.sqlite3") as db:
        db.execute("UPDATE memory_retrievals SET assessment=? WHERE turn_id='broken'", ('{bad json',))
    status = learning_status("telegram:owner")["refinement"]
    assert status["active_verified"] == 0
    assert status["active_records"] == 0  # Corrupt canonical evidence invalidates this pointer.


def test_single_case_assessment_cannot_claim_heldout_verified_gain(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    receipt = stores.memory.add_receipt("Use hyphenated journals.")
    record_markdown("telegram:owner", "add_memory", {"fact": receipt["summary"]}, receipt)
    stores.memory.record_retrieval("one-turn", stores.memory.recall("hyphenated journals")["records"])
    stores.memory.assess_retrieval("one-turn", receipt["record_id"], 1,
                                   checker="fixture-v1", case_id="only", baseline=False,
                                   candidate=True, used=True)
    status = learning_status("telegram:owner")["refinement"]
    assert status["stage"] == "assessed" and status["verified"] == 0
    assert status["heldout_gain_cases"] == 0


def test_status_ignores_malformed_claimed_comparison(stores):
    import json
    import sqlite3
    from zeline.reflection_markdown import learning_status, record_markdown
    receipt = stores.memory.add_receipt("Use hyphenated journals.")
    record_markdown("telegram:owner", "add_memory", {"fact": receipt["summary"]}, receipt)
    stores.memory.record_retrieval("bad-turn", stores.memory.recall("hyphenated journals")["records"])
    stores.memory.assess_retrieval("bad-turn", receipt["record_id"], 1,
                                   checker="fixture-v1", case_id="only", baseline=False,
                                   candidate=True, used=True)
    db_path = stores.root / "memory" / "memory.sqlite3"
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT assessment FROM memory_retrievals WHERE turn_id='bad-turn'").fetchone()
        evidence = json.loads(row[0])
        evidence["comparison"] = {"heldout_gains": "forged"}
        db.execute("UPDATE memory_retrievals SET assessment=? WHERE turn_id='bad-turn'",
                   (json.dumps(evidence),))
    status = learning_status("telegram:owner")["refinement"]
    assert status["verified"] == 0 and status["heldout_gain_cases"] == 0


def test_status_requires_canonical_proposal_and_keeps_it_inactive(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    assert learning_status(identity)["enhancement"]["stage"] == "empty"
    proposal = stores.forge.propose_edit_receipt(action="create", name="status-triage",
                                                content="# Status triage\n\nInspect failures.")
    assert proposal["state"] == "created"
    record_markdown(identity, "manage_skill", {"action": "create", "name": "status-triage"}, proposal)
    status = learning_status(identity)["enhancement"]
    assert status["stage"] == "pending"
    assert status["active"] is False
    assert learning_status("telegram:outsider")["enhancement"]["stage"] == "empty"
    assert not (stores.skills.PRIVATE_SKILLS_DIR / "status-triage" / "SKILL.md").exists()


def test_pending_candidate_paired_test_is_not_live_verified_gain(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    name = "status-triage"
    content = "---\nname: status-triage\ndescription: Test fixture.\n---\n# Triage\n\nmode=safe\n"
    receipt = stores.forge.propose_edit_receipt(action="create", name=name, content=content)
    record_markdown(identity, "manage_skill", {"action": "create", "name": name}, receipt)

    def evaluate(stage):
        safe = "mode=safe" in (stage / "SKILL.md").read_text()
        return {"cases": {"fixture": safe, "guard": True}}

    checks = stores.forge.test_comparison(receipt["record_id"], evaluator=evaluate,
                                          training_case_ids=["guard"], held_out_case_ids=["fixture"])
    assert checks["improvement_proven"] is True
    status = learning_status(identity)["enhancement"]
    assert status["stage"] == "tested" and status["active"] is False
    assert status["paired_gain_cases"] == 1 and status["heldout_gain_cases"] == 1
    assert status["verified"] == 0  # Candidate test is not live outcome assessment.
    assert not (stores.skills.PRIVATE_SKILLS_DIR / name / "SKILL.md").exists()


def test_self_card_labels_offline_paired_gain_as_candidate_only(stores):
    from zeline.gateways.telegram import _self_card
    from zeline.reflection_markdown import record_markdown
    identity = "telegram:owner"
    receipt = stores.forge.propose_edit_receipt(
        action="create", name="status-triage",
        content="---\nname: status-triage\ndescription: Test fixture.\n---\n# Triage\n\nmode=safe\n")
    record_markdown(identity, "manage_skill", {"action": "create", "name": "status-triage"}, receipt)
    stores.forge.test_comparison(
        receipt["record_id"], evaluator=lambda stage: {
            "cases": {"fixture": "mode=safe" in (stage / "SKILL.md").read_text(), "guard": True}},
        training_case_ids=["guard"], held_out_case_ids=["fixture"])
    card = _self_card(identity)
    assert "paired candidate gains 1 (held-out 1; not live proof)" in card
    assert "📙 Self-Enhancement.md : tested (proposal inactive)" in card


def test_malformed_paired_evidence_never_upgrades_status(stores):
    import json
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    receipt = stores.forge.propose_edit_receipt(
        action="create", name="status-triage",
        content="---\nname: status-triage\ndescription: Test fixture.\n---\n# Triage\n\nmode=safe\n")
    record_markdown(identity, "manage_skill", {"action": "create", "name": "status-triage"}, receipt)
    stores.forge.test_comparison(
        receipt["record_id"], evaluator=lambda stage: {
            "cases": {"fixture": "mode=safe" in (stage / "SKILL.md").read_text(), "guard": True}},
        training_case_ids=["guard"], held_out_case_ids=["fixture"])
    with stores.forge.connect() as db:
        row = db.execute("SELECT checks FROM proposals WHERE id=?", (receipt["record_id"],)).fetchone()
        checks = json.loads(row[0])
        checks["evaluation_split"]["training_case_ids"] = [[]]
        db.execute("UPDATE proposals SET checks=? WHERE id=?", (json.dumps(checks), receipt["record_id"]))
    status = learning_status(identity)["enhancement"]
    assert status["paired_gain_cases"] == status["heldout_gain_cases"] == 0
    assert status["verified"] == 0


def test_applied_proposal_is_not_active_if_installed_revision_moved(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    proposal = stores.forge.propose_edit_receipt(action="create", name="status-triage",
                                                content="# Status triage\n\nInspect failures.")
    record_markdown(identity, "manage_skill", {"action": "create", "name": "status-triage"}, proposal)
    with stores.forge.connect() as db:
        db.execute("UPDATE proposals SET status='applied' WHERE id=?", (proposal["record_id"],))
    # Even an applied row is historical once the exact private candidate is absent.
    status = learning_status(identity)["enhancement"]
    assert status["stage"] == "stale" and status["active"] is False


def test_status_skill_use_is_observation_not_measured_gain_and_stale_revision_is_not_current(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    from zeline.skillforge import revision_of
    identity = "telegram:owner"
    entry = stores.skills.PRIVATE_SKILLS_DIR / "status-triage" / "SKILL.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("---\nname: status-triage\ndescription: Triage\n---\n# Triage\n\nInspect failures.\n")
    revision = revision_of(entry.read_text())
    receipt = {"version": 1, "kind": "skill", "state": "updated", "record_id": "patch_fixture",
               "revision": revision, "file_path": "SKILL.md", "replacements": 1, "summary": "Patched"}
    record_markdown(identity, "manage_skill", {"action": "patch", "name": "status-triage"}, receipt)
    assert learning_status(identity)["improvement"]["stage"] == "stored"
    stores.forge.record_usage(identity, "turn-1", "status-triage", "private", revision, "failure")
    status = learning_status(identity)["improvement"]
    assert status["stage"] == "observed"
    assert status["observations"] == 1 and status["verified"] == 0
    assert status["review_suggested"] is True
    entry.write_text(entry.read_text() + "\nInspect logs.\n")
    assert learning_status(identity)["improvement"]["stage"] == "stale"


def test_status_memory_recall_is_not_use_but_trusted_regression_is_and_stays_historical(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    stores.memory.default_source = "reflection"
    receipt = stores.memory.add_receipt("Inspect the source before patching.")
    record_markdown(identity, "add_memory", {"fact": receipt["summary"]}, receipt)
    assert learning_status(identity)["refinement"]["stage"] == "stored"
    selected = stores.memory.recall("source patching")["records"]
    stores.memory.record_retrieval("turn-1", selected)
    status = learning_status(identity)["refinement"]
    assert status["stage"] == "retrieved" and status["verified"] == 0
    stores.memory.assess_retrieval("turn-1", receipt["record_id"], 1, checker="fixture-v1",
                                   case_id="source-choice", baseline=True, candidate=False, used=True)
    status = learning_status(identity)["refinement"]
    assert status["stage"] == "regression" and status["verified"] == 1
    assert status["review_suggested"] is True
    assert stores.memory.records()[0]["revision"] == 2
    assert learning_status("telegram:outsider")["refinement"]["stage"] == "empty"


def test_unattributed_assessment_cannot_promote_recall_to_verified(stores):
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    stores.memory.default_source = "reflection"
    receipt = stores.memory.add_receipt("Prefer source-bound checks.")
    record_markdown(identity, "add_memory", {"fact": "Prefer source-bound checks."}, receipt)
    stores.memory.record_retrieval("turn-2", stores.memory.recall("source-bound checks")["records"])
    stores.memory.assess_retrieval("turn-2", receipt["record_id"], 1, checker="fixture-v1",
                                   case_id="source-bound", baseline=False, candidate=True, used=False)
    status = learning_status(identity)["refinement"]
    assert status["stage"] == "retrieved" and status["verified"] == 0


def test_linked_lesson_correction_is_operational_evidence_not_verified_task_gain(stores):
    from zeline import lessons
    from zeline.reflection_markdown import learning_status, record_markdown
    identity = "telegram:owner"
    lesson_store = lessons.LessonsStore(stores.root / "lessons.db")
    assert lesson_store.record_failure(identity, "read_file", {"path": "missing"}, "not found")
    event = lesson_store.record_success(identity, "read_file", {"path": "existing"})
    receipt = lesson_store.record_fix(identity, "read_file", "missing", "Read the existing file.", event)
    assert isinstance(receipt, dict), receipt
    record_markdown(identity, "resolve_lesson", {"tool": "read_file"}, receipt)
    status = learning_status(identity)["refinement"]
    assert status["stage"] == "linked_success"
    assert status["verified"] == 0 and status["observations"] == 1
    assert learning_status("telegram:other")["refinement"]["stage"] == "empty"


def test_self_command_is_owner_only_and_renders_honest_stages(stores, monkeypatch):
    from zeline.gateways import telegram
    sent = []
    monkeypatch.setattr(telegram, "_api_call", lambda _api, _method, **kw: sent.append(kw["text"]))
    params = dict(api="fixture", text="/self", sessions=object(), identity="telegram:owner",
                  chat_id=42, stop_event=None, tool_profile="full", allowed=[42])
    assert telegram._handle_command_update(**params)
    assert "📙" in sent[-1] and "📘" in sent[-1] and "📗" in sent[-1]
    assert "not evidence of improved answers" in sent[-1]
    assert telegram._handle_command_update(**{**params, "chat_id": 43})
    assert "Access denied" in sent[-1] and "📙" not in sent[-1]
