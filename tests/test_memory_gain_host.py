"""Host-only registration and scheduling of fixed fact-specific evaluation packs."""
from contextlib import closing
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
from types import ModuleType

import pytest


def inert_llm_worker(prompt, store, record_id, revision, mode):
    """Importable under spawn; replace the model only inside this isolated worker."""
    from zeline.memory_gain_automation import llm_answer

    class InertAgent:
        def __init__(self, *, identity, tool_profile):
            assert identity == store.identity and tool_profile == 'safe'
            self.messages = []

        def _call_llm(self, *, use_tools, force_stream):
            assert use_tools is False and force_stream is False
            return {'role': 'assistant', 'content':
                'Self-Refinement.md' if 'Self-Refinement.md' in self.messages[-1]['content'] else 'unknown'}

    # Do not import the real provider stack after the worker socket guard is
    # installed (ssl subclasses socket.socket at import time). This inert module
    # has no provider path at all; the production recall/evidence worker is real.
    original = sys.modules.get('zeline.agent')
    try:
        inert_agent_module = ModuleType('zeline.agent')
        setattr(inert_agent_module, 'Zeline', InertAgent)
        sys.modules['zeline.agent'] = inert_agent_module
        return llm_answer(prompt, store, record_id, revision, mode)
    finally:
        if original is None:
            sys.modules.pop('zeline.agent', None)
        else:
            sys.modules['zeline.agent'] = original


@pytest.mark.parametrize('start_method', sorted({'spawn', multiprocessing.get_start_method()}))
def test_builtin_worker_end_to_end_fresh_process_paired_receipt(tmp_path, monkeypatch, start_method):
    from zeline import config, memory, memory_gain_automation, memory_gain_host
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    # Exercise spawn even on POSIX: no parent-local model patch may be inherited.
    monkeypatch.setattr(memory_gain_automation, '_process_context',
                        lambda **kwargs: multiprocessing.get_context(start_method))
    from zeline.memory_gain_automation import Case, MemoryGainAutomation
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('The hyphenated journal is Self-Refinement.md.')
    # Four fresh worker processes can exceed 4 seconds on a loaded CI host.
    # Keep the job bounded while allowing scheduler/process startup variance.
    service = MemoryGainAutomation(tmp_path / 'learning' / 'evaluations.sqlite3',
                                   store_factory=memory.MemoryStore, timeout=12)
    service.register_pack('journal', [
        Case('train', 'Name the hyphenated journal', 'Self-Refinement.md', 'train'),
        Case('heldout', 'Which hyphenated journal file?', 'Self-Refinement.md', 'heldout')],
        worker='test_memory_gain_host:inert_llm_worker')
    service.enqueue('telegram:owner', receipt['record_id'], 1, 'journal')
    result = service.drain(max_jobs=1, max_seconds=30)[0]
    assert result['status'] == 'completed' and result['report']['qualified'], result
    assert result['report']['train_gains'] == 1
    assert result['report']['heldout_gains'] == 1
    assert len(result['report']['case_evidence']) == 2
    assert len(result['workers']) == 4
    assert all(worker['pid'] != os.getpid() for worker in result['workers'])
    assert len({worker['storage'] for worker in result['workers']}) == 4
    persisted = service.result('telegram:owner', receipt['record_id'], 1, 'journal')
    assert persisted is not None and persisted['report'] == result['report']
    assert not service.allow_network
    assert memory_gain_host.status_for_identity('telegram:owner')['qualified_jobs'] == 1
    assert store.retrievals('eval-does-not-exist') == []


def test_auto_status_counts_only_live_qualified_sandbox_job(tmp_path, monkeypatch):
    from pathlib import Path
    from zeline import config, memory, memory_gain_host
    from zeline.memory_gain_automation import Case, MemoryGainAutomation
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('Owner prefers hyphenated journal names.')
    # This test validates status accounting, not a one-second worker deadline.
    # Fresh evaluation subprocesses can exceed that on a loaded Termux host.
    service = MemoryGainAutomation(tmp_path / 'learning' / 'evaluations.sqlite3',
                                   store_factory=memory.MemoryStore, timeout=12)
    service.register_pack('journal', [Case('a', 'Name hyphenated journal', 'Self-Refinement.md', 'train'),
         Case('b', 'Which hyphenated journal file?', 'Self-Refinement.md', 'heldout')],
         worker='test_memory_gain_automation:fixture_worker')
    service.enqueue('telegram:owner', receipt['record_id'], 1, 'journal')
    result = service.drain(max_jobs=1, max_seconds=20)[0]
    assert result['status'] == 'completed', result
    status = memory_gain_host.status_for_identity('telegram:owner')
    assert status['qualified_jobs'] == 1 and status['heldout_gain_cases'] == 1
    store.remove('hyphenated journal names')
    assert memory_gain_host.status_for_identity('telegram:owner')['qualified_jobs'] == 0


def test_auto_status_fails_closed_without_queue_or_for_forged_report(tmp_path, monkeypatch):
    import sqlite3
    from zeline import config, memory, memory_gain_host
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    assert memory_gain_host.status_for_identity('telegram:owner')['qualified_jobs'] == 0
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('Use hyphenated Self-Refinement.md.')
    from zeline.memory_gain_automation import MemoryGainAutomation
    queue = MemoryGainAutomation(tmp_path / 'learning' / 'evaluations.sqlite3',
                                 store_factory=memory.MemoryStore)
    with closing(sqlite3.connect(queue.path)) as db, db:
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
            ('telegram:owner', receipt['record_id'], 1, 'forged', '0' * 64,
             'completed', 1.0, json.dumps({'status': 'completed',
             'report': {'qualified': True, 'heldout_gains': 99}})))
    assert memory_gain_host.status_for_identity('telegram:owner')['qualified_jobs'] == 0


def test_status_remains_visible_when_queue_file_grows_past_two_megabytes(tmp_path, monkeypatch):
    import sqlite3
    from zeline import config, memory, memory_gain_host
    from zeline.memory_gain_automation import Case, MemoryGainAutomation
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('Use the hyphenated journal.')
    queue = MemoryGainAutomation(tmp_path / 'learning' / 'evaluations.sqlite3',
                                 store_factory=memory.MemoryStore)
    queue.register_pack('journal', [Case('a', 'Name the journal?', 'Self-Refinement.md', 'train'),
                                     Case('b', 'Which file?', 'Self-Refinement.md', 'heldout')],
                        worker='zeline.memory_gain_automation:llm_answer')
    queue.enqueue('telegram:owner', receipt['record_id'], receipt['revision'], 'journal')
    with closing(sqlite3.connect(queue.path)) as db, db:
        db.execute('CREATE TABLE old_results_padding (payload BLOB)')
        db.execute('INSERT INTO old_results_padding VALUES (zeroblob(2100000))')
    assert queue.path.stat().st_size > 2_000_000
    assert memory_gain_host.status_for_identity('telegram:owner')['pending_jobs'] == 1


def test_scheduler_loop_recovers_then_drains_without_spin(monkeypatch):
    import threading
    from zeline import memory_gain_host
    stop = threading.Event()
    calls = []
    def run_pending(*, recover=False, stop=None):
        calls.append(recover)
        stop.set()
    monkeypatch.setattr(memory_gain_host, 'run_pending', run_pending)
    memory_gain_host.scheduler_loop(stop, interval=0.1)
    assert calls == [True]


def test_normal_add_memory_path_enqueues_reviewed_matching_receipt(tmp_path, monkeypatch):
    from zeline import config, memory, memory_gain_host
    from zeline.tools import ToolExecutor
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    seen = []
    monkeypatch.setattr(memory_gain_host, 'enqueue_for_receipt',
                        lambda identity, receipt: seen.append((identity, receipt)) or 1)
    executor = ToolExecutor(identity='telegram:owner', profile='safe')
    assert 'saved' in executor._add_memory_with_journal('Use hyphenated journals.').lower()
    assert len(seen) == 1
    assert seen[0][0] == 'telegram:owner'
    assert seen[0][1]['state'] == 'created'


def test_builtin_model_worker_uses_no_tools_and_fresh_scoped_recall(tmp_path, monkeypatch):
    from zeline import agent, config, memory
    from zeline.memory_gain_automation import llm_answer
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('Use hyphenated Self-Refinement.md for journals.')
    seen = []
    def fake_llm(self, *, use_tools=True, force_stream=None, **kwargs):
        seen.append((use_tools, force_stream, self.messages[-1]['content']))
        return {'role': 'assistant', 'content': 'Self-Refinement.md' if 'Self-Refinement.md' in self.messages[-1]['content'] else 'unknown'}
    monkeypatch.setattr(agent.Zeline, '_call_llm', fake_llm)
    answer, turn = llm_answer('Which hyphenated journal filename?', store,
                              receipt['record_id'], receipt['revision'], 'candidate')
    assert answer == 'Self-Refinement.md'
    assert seen[0][0] is False and seen[0][1] is False
    assert any(r['record_id'] == receipt['record_id'] for r in store.retrievals(turn))


def test_pack_file_with_world_or_group_read_is_rejected(tmp_path, monkeypatch):
    from zeline import config, memory_gain_host
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    directory = tmp_path / 'learning'
    directory.mkdir()
    pack_path = directory / 'case-packs.json'
    pack_path.write_text('not JSON')
    pack_path.chmod(0o644)
    assert memory_gain_host._case_packs() == []


def test_no_case_pack_never_instantiates_evaluation(tmp_path, monkeypatch):
    from zeline import config, memory_gain_host
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    assert memory_gain_host.enqueue_for_receipt('telegram:owner',
        {'state': 'created', 'record_id': 'memory-1', 'revision': 1}) == 0
    assert not (tmp_path / 'learning' / 'evaluations.sqlite3').exists()


def test_matching_reviewed_pack_enqueues_only_exact_changed_fact(tmp_path, monkeypatch):
    from zeline import config, memory, memory_gain_host
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    owner = 'telegram:owner'
    stored = memory.MemoryStore(owner)
    receipt = stored.add_receipt('Use hyphenated Self-Refinement.md.')
    pack = {'version': 1, 'packs': [{'name': 'journal-v1',
            'fact_sha256': hashlib.sha256('Use hyphenated Self-Refinement.md.'.encode()).hexdigest(),
            'worker': 'zeline.memory_gain_automation:llm_answer',
            'cases': [{'id':'train','prompt':'Which filename?','expected':'Self-Refinement.md','split':'train'},
                      {'id':'heldout','prompt':'What journal filename?','expected':'Self-Refinement.md','split':'heldout'}]}]}
    directory = tmp_path / 'learning'; directory.mkdir()
    pack_path = directory / 'case-packs.json'
    pack_path.write_text(json.dumps(pack))
    # Test parsing/matching independently of native ACLs. Windows chmod(0600)
    # is not a privacy guarantee; the unsupported-platform gate is tested below.
    real_stat = Path.stat
    def private_pack_stat(path, *args, **kwargs):
        metadata = real_stat(path, *args, **kwargs)
        if path == pack_path:
            fields = list(metadata)
            fields[0] = stat.S_IFREG | 0o600
            return os.stat_result(fields)
        return metadata
    monkeypatch.setattr(memory_gain_host, '_POSIX_PERMISSIONS', True)
    monkeypatch.setattr(Path, 'stat', private_pack_stat)
    seen = []
    class FakeAutomation:
        def __init__(self, path, **kwargs):
            seen.append(('construct', path, kwargs['allow_network']))
        def register_pack(self, name, cases, *, worker):
            seen.append(('register', name, worker, len(cases)))
        def enqueue(self, owner, record_id, revision, pack):
            seen.append(('enqueue', owner, record_id, revision, pack))
    monkeypatch.setattr(memory_gain_host, 'MemoryGainAutomation', FakeAutomation)
    assert memory_gain_host.enqueue_for_receipt(owner, receipt) == 1
    assert seen[-1] == ('enqueue', owner, receipt['record_id'], receipt['revision'], 'journal-v1')
    assert memory_gain_host.enqueue_for_receipt(owner,
        {**receipt, 'state': 'duplicate'}) == 0
    assert memory_gain_host.enqueue_for_receipt('telegram:other', receipt) == 0


def test_pack_file_rejected_without_posix_permission_support(tmp_path, monkeypatch):
    from zeline import config, memory_gain_host
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    # Exercise the unsupported Windows ACL policy on every platform without
    # changing os.name (which would affect pathlib and other unrelated code).
    assert memory_gain_host._POSIX_PERMISSIONS is (os.name == 'posix')
    monkeypatch.setattr(memory_gain_host, '_POSIX_PERMISSIONS', False)
    directory = tmp_path / 'learning'
    directory.mkdir()
    pack_path = directory / 'case-packs.json'
    pack_path.write_text('not JSON', encoding='utf-8')
    pack_path.chmod(0o600)
    assert memory_gain_host._case_packs() == []
    assert memory_gain_host.enqueue_for_receipt('telegram:owner',
        {'state': 'created', 'record_id': 'memory-1', 'revision': 1}) == 0
    assert not (directory / 'evaluations.sqlite3').exists()
