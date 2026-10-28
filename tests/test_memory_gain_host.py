"""Host-only registration and scheduling of fixed fact-specific evaluation packs."""
import hashlib
import json
from types import SimpleNamespace


def test_builtin_worker_end_to_end_fresh_process_paired_receipt(tmp_path, monkeypatch):
    from zeline import agent, config, memory, memory_gain_host
    from zeline.memory_gain_automation import Case, MemoryGainAutomation
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('The hyphenated journal is Self-Refinement.md.')
    def fake_model(self, *, use_tools, force_stream):
        assert use_tools is False and force_stream is False
        return {'role': 'assistant', 'content':
            'Self-Refinement.md' if 'Self-Refinement.md' in self.messages[-1]['content'] else 'unknown'}
    monkeypatch.setattr(agent.Zeline, '_call_llm', fake_model)
    # Four fresh worker processes can exceed 4 seconds on a loaded CI host.
    # Keep the job bounded while allowing scheduler/process startup variance.
    service = MemoryGainAutomation(tmp_path / 'learning' / 'evaluations.sqlite3',
                                   store_factory=memory.MemoryStore, timeout=12)
    service.register_pack('journal', [
        Case('train', 'Name the hyphenated journal', 'Self-Refinement.md', 'train'),
        Case('heldout', 'Which hyphenated journal file?', 'Self-Refinement.md', 'heldout')],
        worker='zeline.memory_gain_automation:llm_answer')
    service.enqueue('telegram:owner', receipt['record_id'], 1, 'journal')
    # The full suite may leave unrelated daemon threads alive. This test relies
    # on the parent monkeypatch, so force the ordinary fork path; the separate
    # scheduler-thread test covers the forkserver path.
    monkeypatch.setattr('threading.active_count', lambda: 1)
    result = service.drain(max_jobs=1, max_seconds=20)[0]
    assert result['status'] == 'completed' and result['report']['qualified'], result
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
    with sqlite3.connect(queue.path) as db:
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
    with sqlite3.connect(queue.path) as db:
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
    pack_path.chmod(0o600)
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
