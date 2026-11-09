"""Opt-in host-owned case packs for bounded automatic memory evaluation.

Nothing in a model response can register a pack. A pack must be a local reviewed
file matching the exact canonical text of a changed record. No pack => no work.
Private reviewed-file auto packs require POSIX permission checks. Windows ACL
validation is not implemented, so automatic packs stay disabled there; chmod
mode bits on Windows are not evidence that a file is private.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from . import config, memory
from .memory_gain_automation import Case, MemoryGainAutomation

_CASE_FILE = 'case-packs.json'
_WORKER = 'zeline.memory_gain_automation:llm_answer'
_POSIX_PERMISSIONS = os.name == 'posix'


def _case_packs() -> list[dict[str, Any]]:
    if not _POSIX_PERMISSIONS:
        return []  # Fail closed without a supported private-file permission check.
    path = config.DATA_DIR / 'learning' / _CASE_FILE
    if path.is_symlink():
        return []
    if not path.is_file():
        return []
    if path.stat().st_mode & 0o077:
        return []  # Never load a reviewed oracle file readable by other users.
    if path.stat().st_size > 32_768:
        raise ValueError('Case pack file exceeds bounded size')
    document = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(document, dict) or document.get('version') != 1
            or not isinstance(document.get('packs'), list)
            or len(document['packs']) > 8):
        raise ValueError('Invalid reviewed case pack file')
    packs = []
    for pack in document['packs']:
        if not isinstance(pack, dict) or pack.get('worker') != _WORKER:
            raise ValueError('Only the built-in model worker is allowed for automatic packs')
        digest = pack.get('fact_sha256')
        cases = pack.get('cases')
        if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                or not isinstance(cases, list) or not 2 <= len(cases) <= 6
                or not all(isinstance(case, dict) and
                           set(case) == {'id', 'prompt', 'expected', 'split'} for case in cases)):
            raise ValueError('Invalid fact digest or bounded cases')
        packs.append({**pack, 'cases': [Case(**case) for case in cases]})
    return packs


def _service(packs: list[dict[str, Any]]) -> MemoryGainAutomation:
    service = MemoryGainAutomation(config.DATA_DIR / 'learning' / 'evaluations.sqlite3',
        store_factory=memory.MemoryStore, timeout=45, max_pending=8,
        allow_network=bool(config.API_KEY))
    for pack in packs:
        service.register_pack(pack['name'], pack['cases'], worker=pack['worker'])
    return service


def enqueue_for_receipt(identity: str, receipt: dict[str, Any]) -> int:
    """Fast host hook; failures must never invalidate a committed memory write."""
    if receipt.get('state') not in {'created', 'updated'}:
        return 0
    packs = _case_packs()
    if not packs:
        return 0
    record_id, revision = receipt.get('record_id'), receipt.get('revision')
    store = memory.MemoryStore(identity)
    target = next((r for r in store.records() if r['id'] == record_id and r['revision'] == revision), None)
    if target is None:
        return 0
    digest = hashlib.sha256(target['text'].encode('utf-8')).hexdigest()
    selected = [pack for pack in packs if pack['fact_sha256'] == digest]
    if not selected:
        return 0
    service = _service(packs)
    for pack in selected:
        service.enqueue(identity, record_id, revision, pack['name'])
    return len(selected)


def status_for_identity(identity: str) -> dict[str, int]:
    """Bounded queue metrics; qualified is a separate sandbox result, not owner proof."""
    import sqlite3
    from . import memory
    from .memory_gain_checker import summarize_case_evidence
    status = {'pending_jobs': 0, 'completed_jobs': 0, 'failed_jobs': 0,
              'qualified_jobs': 0, 'heldout_gain_cases': 0}
    path = config.DATA_DIR / 'learning' / 'evaluations.sqlite3'
    try:
        if not path.is_file() or path.is_symlink():
            return status
        live = {r['id']: r['revision'] for r in memory.MemoryStore(identity).records()}
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.2)) as db:
            rows = db.execute('SELECT record_id,revision,status,substr(result,1,65537) FROM jobs '
                              'WHERE owner=? ORDER BY created DESC LIMIT 128', (identity,)).fetchall()
        for record_id, revision, state, result in rows:
            if live.get(record_id) != revision:
                continue
            if state == 'pending':
                status['pending_jobs'] += 1
            elif state == 'failed':
                status['failed_jobs'] += 1
            elif state == 'completed':
                status['completed_jobs'] += 1
                try:
                    report = json.loads(result)['report']
                    counts = summarize_case_evidence(report['case_evidence'], report['case_pack_digest'])
                    if (report['qualified'] is True and report['train_gains'] == counts['train_gains']
                            and report['heldout_gains'] == counts['heldout_gains']):
                        status['qualified_jobs'] += 1
                        status['heldout_gain_cases'] += counts['heldout_gains']
                except (TypeError, ValueError, KeyError, AttributeError):
                    pass
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return {'pending_jobs': 0, 'completed_jobs': 0, 'failed_jobs': 0,
                'qualified_jobs': 0, 'heldout_gain_cases': 0}
    return status


def scheduler_loop(stop, *, interval: float = 60.0) -> None:
    """Opt-in gateway loop; no worker/thread is created without registered packs."""
    import logging
    recovered = False
    while not stop.is_set():
        try:
            run_pending(recover=not recovered, stop=stop)
            recovered = True
        except Exception as exc:
            logging.getLogger(__name__).warning('bounded evaluation tick failed: %s', type(exc).__name__)
        stop.wait(interval)


def run_pending(*, recover: bool = False, stop=None) -> list[dict[str, Any]]:
    """Gateway-owned scheduler hook; one bounded job per tick, no extra daemon."""
    packs = _case_packs()
    if not packs:
        return []
    service = _service(packs)
    if recover:
        service.recover_pending()
    return service.drain(max_jobs=1, max_seconds=240, stop_event=stop)
