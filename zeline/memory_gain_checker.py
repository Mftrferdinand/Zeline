"""Explicit host-owned paired checker for recalled memory.

This module never runs on a normal chat turn. Callers must supply isolated,
fresh-session baseline (without the new memory) and candidate (with it)
runners plus host-owned expected answers. A retrieved record and better answer
are measured evidence on these cases, not proof of generalized learning.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Sequence

from .memory import MemoryStore


# Internal host-only capability, never exposed through the model tool registry.
_PAIRED_ISSUER = object()


def summarize_case_evidence(rows: Any, digest: Any) -> dict[str, int | bool]:
    """Recompute bounded counts from digest-bound per-case receipts, never from totals."""
    if not isinstance(rows, list) or not 2 <= len(rows) <= 64:
        raise ValueError('Missing bounded case evidence')
    fields = {'case_id', 'split', 'prompt_digest', 'answer_digest',
              'baseline_digest', 'candidate_digest', 'baseline_pass',
              'candidate_pass', 'candidate_turn_id'}
    ids: set[str] = set()
    turns: set[str] = set()
    totals = {'train_cases': 0, 'heldout_cases': 0, 'train_gains': 0,
              'heldout_gains': 0, 'no_regressions': True}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError('Invalid case evidence')
        case_id, turn, split = row['case_id'], row['candidate_turn_id'], row['split']
        if (not isinstance(case_id, str) or not re.fullmatch(r'[\w.:-]{1,80}', case_id, re.ASCII)
                or not isinstance(turn, str) or not 1 <= len(turn) <= 128
                or split not in {'train', 'heldout'} or case_id in ids or turn in turns):
            raise ValueError('Duplicate or invalid case evidence')
        ids.add(case_id)
        turns.add(turn)
        if any(not isinstance(row[key], str) or not re.fullmatch('[0-9a-f]{64}', row[key])
               for key in ('prompt_digest', 'answer_digest', 'baseline_digest',
                           'candidate_digest')):
            raise ValueError('Invalid case evidence digest')
        before, after = row['baseline_pass'], row['candidate_pass']
        if (type(before) is not bool or type(after) is not bool
                or before != (row['baseline_digest'] == row['answer_digest'])
                or after != (row['candidate_digest'] == row['answer_digest'])):
            raise ValueError('Invalid case evidence verdict')
        totals[split + '_cases'] += 1
        totals[split + '_gains'] += int(after and not before)
        totals['no_regressions'] &= not (before and not after)
    computed = hashlib.sha256(json.dumps(rows, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    if (digest != computed or not totals['train_cases'] or not totals['heldout_cases']
            or not totals['heldout_gains'] or not totals['no_regressions']
            or any(not row['candidate_pass'] for row in rows)):
        raise ValueError('Unqualified case evidence')
    return totals


def evaluate_memory_gain(store: MemoryStore, record_id: str, revision: int, *,
                         cases: Sequence[Any], checker: str,
                         baseline_runner: Callable[[str], str],
                         candidate_runner: Callable[[str], tuple[str, str]]) -> dict[str, Any]:
    """Compare frozen host cases; persist a held-out assessment only after full qualification."""
    if (not re.fullmatch(r'[\w.:-]{1,80}', checker, flags=re.ASCII)
            or not isinstance(record_id, str) or type(revision) is not int or revision < 1
            or baseline_runner is candidate_runner):
        raise ValueError('A valid host checker, target revision and separate runners are required')
    frozen = tuple(cases)
    if not 2 <= len(frozen) <= 64:
        raise ValueError('Expected bounded train and heldout cases')
    ids: set[str] = set()
    prompts: set[str] = set()
    splits: set[str] = set()
    for case in frozen:
        case_id, prompt, expected, split = (getattr(case, key, None) for key in
                                            ('id', 'prompt', 'expected', 'split'))
        if (not isinstance(case_id, str) or not re.fullmatch(r'[\w.:-]{1,80}', case_id, flags=re.ASCII)
                or not isinstance(prompt, str) or not 1 <= len(prompt) <= 1000
                or not isinstance(expected, str) or not 1 <= len(expected) <= 1000
                or split not in {'train', 'heldout'}):
            raise ValueError('Invalid host-owned case')
        canonical = ' '.join(prompt.casefold().split())
        if case_id in ids or canonical in prompts:
            raise ValueError('Train and heldout prompts/IDs must be disjoint')
        ids.add(case_id)
        prompts.add(canonical)
        splits.add(split)
    if splits != {'train', 'heldout'}:
        raise ValueError('Both train and heldout splits are required')

    # Baselines run before the candidate to avoid accidental memory exposure.
    baseline = [baseline_runner(case.prompt) for case in frozen]
    if any(not isinstance(answer, str) for answer in baseline):
        raise ValueError('Baseline must return an answer string for each case')
    candidate = [candidate_runner(case.prompt) for case in frozen]
    seen_turns: set[str] = set()
    for answer, turn_id in candidate:
        if not isinstance(answer, str) or not isinstance(turn_id, str) or turn_id in seen_turns:
            raise ValueError('Candidate must supply a distinct real turn per case')
        seen_turns.add(turn_id)
        if not any(row['record_id'] == record_id and row['revision'] == revision
                   for row in store.retrievals(turn_id)):
            raise ValueError('Candidate turn lacks the targeted identity/revision retrieval')
    passed = [answer == case.expected for case, (answer, _) in zip(frozen, candidate, strict=True)]
    before = [value == case.expected for case, value in zip(frozen, baseline, strict=True)]
    gains = [not old and new for old, new in zip(before, passed, strict=True)]
    train_gains = sum(case.split == 'train' and gain for case, gain in zip(frozen, gains, strict=True))
    heldout_gains = sum(case.split == 'heldout' and gain for case, gain in zip(frozen, gains, strict=True))
    no_regressions = not any(old and not new for old, new in zip(before, passed, strict=True))
    qualified = bool(all(passed) and no_regressions and heldout_gains)
    case_evidence = [
        {'case_id': case.id, 'split': case.split,
         'prompt_digest': hashlib.sha256(case.prompt.encode()).hexdigest(),
         'answer_digest': hashlib.sha256(case.expected.encode()).hexdigest(),
         'baseline_digest': hashlib.sha256(old.encode()).hexdigest(),
         'candidate_digest': hashlib.sha256(answer.encode()).hexdigest(),
         'baseline_pass': before[index], 'candidate_pass': passed[index],
         'candidate_turn_id': turn_id}
        for index, (case, old, (answer, turn_id)) in enumerate(
            zip(frozen, baseline, candidate, strict=True))]
    pack_digest = hashlib.sha256(json.dumps(case_evidence, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    report = {'checker': checker, 'qualified': qualified, 'train_gains': train_gains,
              'heldout_gains': heldout_gains, 'no_regressions': no_regressions,
              'case_pack_digest': pack_digest, 'case_evidence': case_evidence}
    if qualified:
        index = next(i for i, (case, gain) in enumerate(zip(frozen, gains, strict=True))
                     if case.split == 'heldout' and gain)
        comparison = {'train_cases': sum(case.split == 'train' for case in frozen),
                      'heldout_cases': sum(case.split == 'heldout' for case in frozen),
                      'train_gains': train_gains, 'heldout_gains': heldout_gains,
                      'no_regressions': True}
        assessment = store.assess_retrieval(candidate[index][1], record_id, revision,
                                            checker=checker, case_id=frozen[index].id,
                                            baseline=False, candidate=True, used=True,
                                            comparison=comparison, _issuer=_PAIRED_ISSUER,
                                            _case_evidence=case_evidence,
                                            _case_pack_digest=pack_digest)
        report['evidence_id'] = assessment['evidence_id']
    return report
