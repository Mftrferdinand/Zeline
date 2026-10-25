"""Opt-in handoff from training-only GEPA search to host-owned held-out evaluation.

The optimizer receives training IDs and inputs, never held-out cases. The host
freezes one training-selected candidate, evaluates a pinned adapter in separate
workers, and leaves review/apply to explicit operator APIs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from .gepa_training import search_training_bounded
from .offline_evaluation import _validate_manifest, evaluate, freeze_evaluation
from .skillforge import _tree_digest, revision_of


def evaluate_training_search(
    *, store, name: str, rationale: str, seed_text: str,
    proposals: list[str], manifest: dict[str, Any], adapter_path,
    adapter_sha256: str, score_case: Callable[[str, dict[str, Any]], bool],
    max_metric_calls: int, max_proposals: int, timeout: float,
    task_timeout: float = 5.0, total_timeout: float = 60.0,
) -> dict[str, Any]:
    """Stage and test one training winner; never review, apply or publish it."""
    frozen_manifest, _ = _validate_manifest(manifest)
    source, scope = store._source(name)
    if source is None or revision_of(source.read_text(encoding='utf-8')) != revision_of(seed_text):
        raise ValueError('Training seed must match the active skill')
    initial_tree = _tree_digest(source)
    path = Path(adapter_path).expanduser().resolve(strict=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != adapter_sha256:
        raise ValueError('Trusted adapter digest mismatch before search')
    training = [{'id': case['id'], 'input': case['input']}
                for case in frozen_manifest['training']]
    # No held-out item or label is passed to the optimizer. score_case is
    # operator-trusted and should only inspect the supplied training case.
    report = search_training_bounded(
        seed_text=seed_text, training=training, score_case=score_case,
        proposals=proposals, max_metric_calls=max_metric_calls,
        max_proposals=max_proposals, timeout=timeout,
    )
    candidates = report['candidates']
    scores = report['training_scores']
    selected = report['selected_index']
    if (not candidates or candidates[0] != seed_text or len(scores) != len(candidates)
            or type(selected) is not int or not 0 <= selected < len(candidates)
            or selected == 0 or scores[selected] <= scores[0]):
        raise ValueError('GEPA found no training improvement over the active seed')
    current_source, current_scope = store._source(name)
    if (current_source != source or current_scope != scope or
            revision_of(source.read_text(encoding='utf-8')) != revision_of(seed_text) or
            _tree_digest(current_source) != initial_tree):
        raise ValueError('Active skill or companions changed during training search')
    proposal = store.propose(name, candidates[selected], rationale)
    if (proposal['base'] != revision_of(seed_text) or proposal['base_scope'] != scope or
            proposal['source_tree'] != initial_tree):
        raise ValueError('Active skill or companions moved before evaluation could be frozen')
    frozen = freeze_evaluation(
        store, proposal['id'], frozen_manifest,
        adapter_path=path, adapter_sha256=adapter_sha256,
        task_timeout=task_timeout, total_timeout=total_timeout,
    )
    evidence = evaluate(store, frozen)
    return {
        **evidence,
        'optimizer_executed': True,
        'report_only': True,
        'proposal_id': proposal['id'],
        'training': {
            'scope': 'training_only', 'selected_index': selected,
            'selected_sha256': revision_of(candidates[selected]),
            'metric_calls': report['metric_calls'], 'proposals': report['proposals'],
        },
    }
