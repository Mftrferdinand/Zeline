"""Paired fixture contracts are measurements, not general model-quality proof."""
import importlib
import pytest


@pytest.fixture
def forge(tmp_path, monkeypatch):
    from zeline import skills
    module = importlib.import_module('zeline.skillforge')
    monkeypatch.setattr(skills, 'PRIVATE_SKILLS_DIR', tmp_path / 'private')
    monkeypatch.setattr(skills, 'PUBLIC_SKILLS_DIR', tmp_path / 'public')
    skills.PRIVATE_SKILLS_DIR.mkdir()
    return module.ForgeStore(tmp_path / 'forge'), skills


def content(body):
    return '---\nname: fixture-procedure\ndescription: Handle the controlled fixture.\n---\n# Fixture\n' + body + '\n'


def test_proposal_receipt_credits_only_actual_insert(forge):
    store, _ = forge
    first = store.propose_edit_receipt(action='create', name='fixture-procedure', content=content('Use safe mode.'))
    duplicate = store.propose_edit_receipt(action='create', name='fixture-procedure', content=content('Use safe mode.'))
    assert first['state'] == 'created' and first['kind'] == 'skill'
    assert duplicate['state'] == 'duplicate'
    assert duplicate['record_id'] == first['record_id']
    assert first['revision'] == store.get(first['record_id'])['candidate']
    assert len(store.proposals()) == 1


def test_active_noop_has_no_new_proposal(forge):
    store, skills = forge
    path = skills.PRIVATE_SKILLS_DIR / 'fixture-procedure.md'
    path.write_text(content('Use safe mode.'))
    receipt = store.propose_edit_receipt(action='patch', name='fixture-procedure',
                                       old_text='safe mode', new_text='safe mode')
    assert receipt['state'] == 'unchanged'
    assert store.proposals() == []


def test_paired_task_contract_then_review_apply_and_revision_observations(forge):
    store, skills = forge
    path = skills.PRIVATE_SKILLS_DIR / 'fixture-procedure.md'
    path.write_text(content('mode=unsafe'))
    proposal = store.propose('fixture-procedure', content('mode=safe'), 'Correct the observed fixture failure')
    calls = []
    def evaluate(stage):
        text = (stage / 'SKILL.md').read_text()
        # Execute the same task contract on both snapshots, not just lint docs.
        selected_mode = 'safe' if 'mode=safe' in text else 'unsafe'
        result = {'accepted': selected_mode == 'safe', 'output': selected_mode}
        calls.append(result)
        return {'cases': {'fixture-mode': result['accepted'], 'guard': '# Fixture' in text}}
    checks = store.test_comparison(proposal['id'], evaluator=evaluate)
    assert calls[0]['accepted'] is False and calls[1]['accepted'] is True
    assert checks['improvement_proven'] is True
    assert checks['comparison']['gains'] == ['fixture-mode']
    assert checks['comparison']['regressions'] == []
    store.review(proposal['id'], approve=True, reviewer='fixture-operator')
    applied = store.apply(proposal['id'])
    store.record_usage('owner', 'turn1', proposal['name'], 'private', applied['candidate'], 'unknown')
    store.record_usage('owner', 'turn2', proposal['name'], 'private', applied['candidate'], 'success', tool_successes=1)
    report = store.proposal_outcomes(proposal['id'], identity='owner')
    assert report['observations']['unknown'] == 1
    assert report['observations']['success'] == 1
    assert report['causal_improvement_proven'] is False
    assert store.proposal_outcomes(proposal['id'], identity='someone-else')['observations']['success'] == 0


@pytest.mark.parametrize('failure', ['regression', 'no_gain', 'mismatched_cases', 'mutation'])
def test_comparison_rejects_invalid_or_unhelpful_evidence(forge, failure):
    store, skills = forge
    (skills.PRIVATE_SKILLS_DIR / 'fixture-procedure.md').write_text(content('baseline'))
    proposal = store.propose('fixture-procedure', content('candidate'), 'Test the fixture contrast')
    def evaluate(stage):
        candidate = 'candidate' in (stage / 'SKILL.md').read_text()
        if failure == 'mutation' and candidate:
            (stage / 'extra.txt').write_text('unreviewed')
        if failure == 'mismatched_cases':
            return {'cases': {'other' if candidate else 'fixture': True}}
        if failure == 'no_gain':
            return {'cases': {'fixture': True}}
        return {'cases': {'gain': candidate, 'guard': not candidate}}
    checks = store.test_comparison(proposal['id'], evaluator=evaluate)
    assert checks['improvement_proven'] is False
    with pytest.raises(ValueError):
        store.review(proposal['id'], approve=True, reviewer='fixture-operator')


def test_proposal_receipt_keeps_original_review_origin_and_actual_guidance(forge):
    store, _ = forge
    first = store.propose_edit_receipt(action='create', name='fixture-procedure',
        content=content('Use safe mode for the controlled fixture.'), origin_turn='review-first')
    duplicate = store.propose_edit_receipt(action='create', name='fixture-procedure',
        content=content('Use safe mode for the controlled fixture.'), origin_turn='review-second')
    assert first['origin_turn'] == 'review-first'
    assert duplicate['origin_turn'] == 'review-first'
    assert 'Use safe mode' in first['summary']
