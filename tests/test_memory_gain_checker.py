"""Host-only paired memory checker; fixtures never stand in for live model proof."""
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    expected: str
    split: str


@pytest.fixture
def context(tmp_path, monkeypatch):
    from zeline import config, memory
    from zeline.reflection_markdown import record_markdown

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / "memory")
    store = memory.MemoryStore("telegram:owner")
    receipt = store.add_receipt("Owner prefers hyphenated journal names.")
    record_markdown("telegram:owner", "add_memory", {"fact": receipt["summary"]}, receipt)
    return store, receipt


def _cases():
    return [Case("train", "Name the journal", "Self-Refinement.md", "train"),
            Case("unseen", "Which file records refinement?", "Self-Refinement.md", "heldout")]


def test_checker_promotes_real_retrieved_revision_only_after_heldout_gain(context):
    from zeline.memory_gain_checker import evaluate_memory_gain
    from zeline.reflection_markdown import learning_status

    store, receipt = context
    def baseline(_prompt):
        return "unknown"
    def candidate(prompt):
        turn_id = "turn-" + ("train" if prompt == _cases()[0].prompt else "heldout")
        store.record_retrieval(turn_id, store.recall("hyphenated journal")['records'])
        return "Self-Refinement.md", turn_id

    report = evaluate_memory_gain(store, receipt["record_id"], receipt["revision"],
                                  cases=_cases(), checker="fixture-v1",
                                  baseline_runner=baseline, candidate_runner=candidate)
    assert report["qualified"] is True
    assert report["heldout_gains"] == 1
    assert report["train_gains"] == 1
    assessment = store.retrievals("turn-heldout")[0]["assessment"]
    assert assessment["outcome"] == "gain"
    assert len(assessment["case_evidence"]) == 2
    assert {row["case_id"] for row in assessment["case_evidence"]} == {"train", "unseen"}
    assert all(len(row["prompt_digest"]) == 64 and len(row["answer_digest"]) == 64
               for row in assessment["case_evidence"])
    assert all("prompt" not in row and "answer" not in row for row in assessment["case_evidence"])
    assert assessment["case_pack_digest"] == report["case_pack_digest"]
    status = learning_status("telegram:owner")["refinement"]
    assert status["stage"] == "gain" and status["verified"] == 1
    assert status["heldout_gain_cases"] == 1
    from zeline.gateways.telegram import _self_card
    assert "paired measured gains 2 (held-out 1; bounded cases only)" in _self_card("telegram:owner")
    assert learning_status("telegram:other")["refinement"]["stage"] == "empty"


def test_public_single_case_api_cannot_spoof_qualified_comparison(context):
    store, receipt = context
    turn = 'spoofed-turn'
    store.record_retrieval(turn, store.recall('hyphenated journal')['records'])
    import pytest
    with pytest.raises(ValueError, match='checker-issued'):
        store.assess_retrieval(turn, receipt['record_id'], receipt['revision'],
                               checker='manual', case_id='unseen', baseline=False,
                               candidate=True, used=True,
                               comparison={'train_cases': 1, 'heldout_cases': 1,
                                           'train_gains': 1, 'heldout_gains': 1,
                                           'no_regressions': True})
    assert store.retrievals(turn)[0]['assessment'] is None


def test_internal_issuer_rejects_invented_case_counts_without_case_rows(context):
    from zeline.memory_gain_checker import _PAIRED_ISSUER
    store, receipt = context
    store.record_retrieval('fake', store.recall('hyphenated journal')['records'])
    with pytest.raises(ValueError, match='case evidence'):
        store.assess_retrieval('fake', receipt['record_id'], receipt['revision'],
            checker='fixture-v1', case_id='unseen', baseline=False, candidate=True,
            used=True, comparison={'train_cases':1,'heldout_cases':1,
                                   'train_gains':1,'heldout_gains':1,'no_regressions':True},
            _issuer=_PAIRED_ISSUER)
    assert store.retrievals('fake')[0]['assessment'] is None


def test_tampered_paired_totals_do_not_count_as_verified(context):
    import json
    import sqlite3
    from zeline.memory_gain_checker import evaluate_memory_gain
    from zeline.reflection_markdown import learning_status
    from zeline import memory
    store, receipt = context
    def candidate(prompt):
        turn = 'turn-' + ('train' if prompt == _cases()[0].prompt else 'heldout')
        store.record_retrieval(turn, store.recall('hyphenated journal')['records'])
        return 'Self-Refinement.md', turn
    evaluate_memory_gain(store, receipt['record_id'], receipt['revision'],
                         cases=_cases(), checker='fixture-v1',
                         baseline_runner=lambda _: 'unknown', candidate_runner=candidate)
    with sqlite3.connect(memory.MEMORY_DIR / 'memory.sqlite3') as db:
        row = db.execute('SELECT assessment FROM memory_retrievals WHERE turn_id=?',
                         ('turn-heldout',)).fetchone()
        assessment = json.loads(row[0]); assessment['comparison']['train_gains'] = 0
        db.execute('UPDATE memory_retrievals SET assessment=? WHERE turn_id=?',
                   (json.dumps(assessment), 'turn-heldout'))
    assert learning_status('telegram:owner')['refinement']['verified'] == 0


def test_paired_status_drops_gain_if_any_case_retrieval_disappears(context):
    import sqlite3
    from zeline import memory
    from zeline.memory_gain_checker import evaluate_memory_gain
    from zeline.reflection_markdown import learning_status
    store, receipt = context
    def candidate(prompt):
        turn = 'turn-' + ('train' if prompt == _cases()[0].prompt else 'heldout')
        store.record_retrieval(turn, store.recall('hyphenated journal')['records'])
        return 'Self-Refinement.md', turn
    evaluate_memory_gain(store, receipt['record_id'], receipt['revision'],
                         cases=_cases(), checker='fixture-v1',
                         baseline_runner=lambda _: 'unknown', candidate_runner=candidate)
    with sqlite3.connect(memory.MEMORY_DIR / 'memory.sqlite3') as db:
        db.execute('DELETE FROM memory_retrievals WHERE turn_id=?', ('turn-train',))
    assert learning_status('telegram:owner')['refinement']['verified'] == 0


def test_checker_rejects_duplicate_prompts_and_missing_retrieval(context):
    from zeline.memory_gain_checker import evaluate_memory_gain
    from zeline.reflection_markdown import learning_status

    store, receipt = context
    bad = [*_cases()]
    bad[1] = Case("unseen", bad[0].prompt, "Self-Refinement.md", "heldout")
    with pytest.raises(ValueError, match="disjoint"):
        evaluate_memory_gain(store, receipt["record_id"], 1, cases=bad, checker="fixture-v1",
                             baseline_runner=lambda _: "unknown",
                             candidate_runner=lambda _: ("Self-Refinement.md", "turn-1"))
    with pytest.raises(ValueError, match="retrieval"):
        evaluate_memory_gain(store, receipt["record_id"], 1, cases=_cases(), checker="fixture-v1",
                             baseline_runner=lambda _: "unknown",
                             candidate_runner=lambda _: ("Self-Refinement.md", "turn-1"))
    assert learning_status("telegram:owner")["refinement"]["verified"] == 0


def test_checker_refuses_heldout_gain_if_any_training_case_regresses(context):
    from zeline.memory_gain_checker import evaluate_memory_gain
    from zeline.reflection_markdown import learning_status

    store, receipt = context
    cases = _cases()
    def candidate(prompt):
        turn_id = "turn-" + ("train" if prompt == cases[0].prompt else "heldout")
        store.record_retrieval(turn_id, store.recall("hyphenated journal")['records'])
        return ("wrong" if turn_id == "turn-train" else "Self-Refinement.md"), turn_id
    report = evaluate_memory_gain(store, receipt["record_id"], 1, cases=cases, checker="fixture-v1",
                                  baseline_runner=lambda prompt: ("Self-Refinement.md" if prompt == cases[0].prompt else "unknown"),
                                  candidate_runner=candidate)
    assert report["no_regressions"] is False and report["heldout_gains"] == 1
    assert report["qualified"] is False
    assert learning_status("telegram:owner")["refinement"]["verified"] == 0


def test_checker_does_not_promote_when_heldout_unchanged_or_candidate_regresses(context):
    from zeline.memory_gain_checker import evaluate_memory_gain
    from zeline.reflection_markdown import learning_status

    store, receipt = context
    def candidate(prompt):
        turn_id = "turn-" + ("train" if prompt == _cases()[0].prompt else "heldout")
        store.record_retrieval(turn_id, store.recall("hyphenated journal")['records'])
        return ("Self-Refinement.md" if turn_id == "turn-train" else "unknown"), turn_id
    report = evaluate_memory_gain(store, receipt["record_id"], 1, cases=_cases(), checker="fixture-v1",
                                  baseline_runner=lambda _: "unknown", candidate_runner=candidate)
    assert report["qualified"] is False and report["heldout_gains"] == 0
    assert learning_status("telegram:owner")["refinement"]["verified"] == 0
