"""The /self card must distinguish bounded aggregate history from latest entry."""

def test_self_card_labels_automatic_sandbox_gain_separately(monkeypatch):
    from zeline import reflection_markdown, memory_gain_host
    from zeline.gateways.telegram import _self_card
    summary = dict(stage='stored', active=True, observations=0, verified=0,
                   review_suggested=False, paired_gain_cases=0, heldout_gain_cases=0)
    monkeypatch.setattr(reflection_markdown, 'learning_status', lambda identity: {
        channel: dict(summary) for channel in ('enhancement', 'improvement', 'refinement')})
    monkeypatch.setattr(memory_gain_host, 'status_for_identity', lambda identity: {
        'pending_jobs': 1, 'completed_jobs': 2, 'failed_jobs': 0,
        'qualified_jobs': 1, 'heldout_gain_cases': 1})
    card = _self_card('telegram:owner')
    assert 'sandbox' in card and 'bounded held-out 1' in card
    assert 'not owner live gain' in card


def test_self_card_displays_canonical_aggregate_without_latest_only_label(monkeypatch):
    from zeline import reflection_markdown
    from zeline.gateways.telegram import _self_card
    summary = dict(stage='stored', active=True, observations=2, verified=1,
                   review_suggested=False, paired_gain_cases=0, heldout_gain_cases=0,
                   active_records=2,
                   active_observations=5, active_verified=1,
                   active_paired_gain_cases=0, active_heldout_gain_cases=0)
    monkeypatch.setattr(reflection_markdown, 'learning_status', lambda identity: {
        'enhancement': dict(summary), 'improvement': dict(summary),
        'refinement': dict(summary)})
    card = _self_card('telegram:owner')
    assert 'active 2' in card
    assert 'active observed 5' in card
    assert 'active verified 1' in card
    assert 'latest journal entry only' not in card
