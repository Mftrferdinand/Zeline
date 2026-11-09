"""Host-owned automatic memory evaluation, exercised with real fresh worker processes."""
from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import time
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    expected: str
    split: str


def fixture_worker(prompt, store, record_id, revision, mode):
    """Trusted worker fixture; its storage and PID are reported for isolation checks."""
    from pathlib import Path
    marker = Path(os.environ['HOME']) / 'marker'
    assert not marker.exists()
    marker.write_text(str(os.getpid()))
    if 'timeout' in prompt:
        time.sleep(2)
    if mode == 'baseline':
        assert not any(r['id'] == record_id for r in store.records()), 'baseline leaked target memory'
    if mode == 'candidate':
        selected = store.recall('hyphenated journal')['records']
        import hashlib
        turn = f'{os.getpid()}-{hashlib.sha256(prompt.encode()).hexdigest()[:12]}'
        store.record_retrieval(turn, selected)
        return 'Self-Refinement.md', turn
    return 'unknown', None


@pytest.fixture
def service(tmp_path, monkeypatch):
    from zeline import config, memory
    from zeline.memory_gain_automation import MemoryGainAutomation
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'owner')
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'owner' / 'memory')
    store = memory.MemoryStore('telegram:owner')
    receipt = store.add_receipt('Owner prefers hyphenated journal names.')
    path = tmp_path / 'jobs.sqlite3'
    # Successful workers need import/startup headroom on loaded POSIX hosts too.
    # Timeout behavior is tested separately with an explicit 0.2-second budget.
    runner = MemoryGainAutomation(path, store_factory=memory.MemoryStore,
                                  timeout=5.0,
                                  max_pending=4)
    cases = [Case('train', 'Name hyphenated journal', 'Self-Refinement.md', 'train'),
             Case('heldout', 'Which hyphenated journal file?', 'Self-Refinement.md', 'heldout')]
    runner.register_pack('journal-v1', cases, worker='test_memory_gain_automation:fixture_worker')
    monkeypatch.syspath_prepend(str(__import__('pathlib').Path(__file__).parent))
    return runner, store, receipt, path


def test_scheduler_thread_uses_safe_process_start_and_completes(service, monkeypatch):
    import threading
    from zeline import memory_gain_automation as automation
    real_context = automation.mp.get_context
    methods = []
    def tracked_context(method):
        methods.append(method)
        return real_context(method)
    monkeypatch.setattr(automation.mp, 'get_context', tracked_context)
    runner, _, receipt, _ = service
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    output = []
    thread = threading.Thread(target=lambda: output.extend(runner.drain(max_jobs=1, max_seconds=12)))
    thread.start()
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert output[0]['status'] == 'completed'
    assert ('spawn' if os.name == 'nt' else 'forkserver') in methods


def test_cancel_running_job_kills_worker_before_timeout(service):
    import threading
    import time
    runner, store, receipt, _ = service
    runner.register_pack('slow', [Case('a', 'timeout hyphenated', 'x', 'train'),
                                  Case('b', 'fast hyphenated', 'x', 'heldout')],
                         worker='test_memory_gain_automation:fixture_worker')
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'slow')
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    start = time.monotonic()
    result = runner.drain(max_jobs=1, max_seconds=8, stop_event=stop)
    timer.join()
    assert time.monotonic() - start < 1.5
    assert result[0]['status'] == 'failed'


def test_trusted_pack_only_and_idempotent_revision_jobs(service):
    runner, store, receipt, path = service
    with pytest.raises(ValueError, match='registered'):
        runner.enqueue('telegram:owner', receipt['record_id'], 1, 'invented')
    with pytest.raises(ValueError, match='revision'):
        runner.enqueue('telegram:owner', receipt['record_id'], 99, 'journal-v1')
    first = runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    assert runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1') == first
    assert runner.pending() == 1
    assert runner.drain(max_jobs=0) == []
    assert runner.pending() == 1
    assert path.exists()


def test_real_child_isolation_and_no_owner_db_writes(service):
    runner, store, receipt, _ = service
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    before = store.db_path.read_bytes()
    results = runner.drain(max_jobs=1, max_seconds=12)
    assert len(results) == 1 and results[0]['status'] == 'completed'
    assert results[0]['report']['qualified'] is True
    assert results[0]['report']['heldout_gains'] == 1
    assert len(results[0]['workers']) == 4
    assert len({w['pid'] for w in results[0]['workers']}) == 4
    assert len({w['storage'] for w in results[0]['workers']}) == 4
    assert store.db_path.read_bytes() == before
    assert runner.pending() == 0
    assert runner.result('telegram:owner', receipt['record_id'], 1, 'journal-v1')['report']['qualified']
    assert runner.drain(max_jobs=1) == []


def test_queue_capacity_and_pack_mutation_rejected(service):
    runner, _, receipt, _ = service
    runner.max_pending = 1
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    runner.register_pack('second', [Case('a', 'one', 'x', 'train'),
                                    Case('b', 'two', 'x', 'heldout')],
                         worker='test_memory_gain_automation:fixture_worker')
    with pytest.raises(ValueError, match='full'):
        runner.enqueue('telegram:owner', receipt['record_id'], 1, 'second')
    runner.register_pack('journal-v1', [Case('a', 'one', 'x', 'train'),
                                        Case('b', 'two', 'x', 'heldout')],
                         worker='test_memory_gain_automation:fixture_worker')
    with pytest.raises(ValueError, match='changed'):
        runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')


def test_lost_coordinator_recovery(service):
    runner, store, receipt, path = service
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE jobs SET status='running'")
    assert runner.pending() == 0
    assert runner.recover_pending() == 1
    assert runner.pending() == 1
    assert runner.drain(max_jobs=1, max_seconds=10)[0]['status'] == 'completed'


def test_network_opt_in_is_host_only(service):
    runner, _, _, _ = service
    assert runner.allow_network is False
    with pytest.raises(ValueError, match='network'):
        runner.enqueue('telegram:owner', 'untrusted', 1, 'network', allow_network=True)


def test_result_does_not_store_raw_case_text(service):
    runner, _, receipt, path = service
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    runner.drain(max_jobs=1, max_seconds=10)
    assert 'Name hyphenated journal' not in path.read_bytes().decode('utf-8', errors='ignore')
    assert runner.result('telegram:owner', receipt['record_id'], 1, 'journal-v1') is not None


def test_no_implicit_execution_on_enqueue(service):
    runner, store, receipt, _ = service
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    assert runner.result('telegram:owner', receipt['record_id'], 1, 'journal-v1') is None
    assert not list(store.retrievals('never-run'))


def test_persistence_and_bounded_fail_closed_timeout(service):
    from zeline.memory_gain_automation import MemoryGainAutomation
    runner, store, receipt, path = service
    runner.register_pack('slow', [Case('a', 'timeout hyphenated', 'x', 'train'),
                                  Case('b', 'fast hyphenated', 'x', 'heldout')],
                         worker='test_memory_gain_automation:fixture_worker')
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'slow')
    replacement = MemoryGainAutomation(path, store_factory=type(store), timeout=0.2)
    replacement.register_pack('slow', [Case('a', 'timeout hyphenated', 'x', 'train'),
                                       Case('b', 'fast hyphenated', 'x', 'heldout')],
                              worker='test_memory_gain_automation:fixture_worker')
    outcome = replacement.drain(max_jobs=1, max_seconds=3)
    assert outcome[0]['status'] == 'failed'
    assert 'timeout' in outcome[0]['error'].lower()
    assert store.retrievals('fake') == []


def test_rejects_invalid_pack_and_untrusted_worker_spec(service):
    runner, _, _, _ = service
    with pytest.raises(ValueError):
        runner.register_pack('bad', [Case('a', 'one', 'yes', 'train')],
                             worker='test_memory_gain_automation:fixture_worker')
    with pytest.raises(ValueError):
        runner.register_pack('evil', [Case('a', 'one', 'yes', 'train'),
                                     Case('b', 'two', 'yes', 'heldout')],
                             worker='foo; rm -rf /:run')
    with pytest.raises(ValueError):
        runner.register_pack('oracle', [Case('a', 'one', '', 'train'),
                                       Case('b', 'two', 'yes', 'heldout')],
                             worker='test_memory_gain_automation:fixture_worker')

def test_no_network_default_fixture(service):
    from zeline.memory_gain_automation import MemoryGainAutomation
    runner, store, receipt, path = service
    # Worker attempts network before returning; default deny prevents evaluation.
    runner.register_pack('network', [Case('a', 'net one', 'x', 'train'),
                                     Case('b', 'net two', 'x', 'heldout')],
                         worker='test_memory_gain_automation:network_worker')
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'network')
    result = runner.drain(max_jobs=1, max_seconds=8)
    assert result[0]['status'] == 'failed'
    assert 'network disabled' in result[0]['error'].lower()


def test_nonblocking_drain_lock_contention_and_recovery(service):
    from zeline import memory_gain_automation as automation
    runner, _, _, _ = service
    with automation._drain_lock(runner.path) as acquired:
        assert acquired
        assert runner.drain(max_jobs=0) == []
        with pytest.raises(RuntimeError, match='still active'):
            runner.recover_pending()
    assert runner.recover_pending() == 0


def lock_probe(path, conn):
    from zeline.memory_gain_automation import _drain_lock
    try:
        with _drain_lock(path) as acquired:
            conn.send(acquired)
    finally:
        conn.close()


def test_lock_contention_across_spawned_processes(tmp_path):
    import multiprocessing
    from zeline.memory_gain_automation import _drain_lock
    path = tmp_path / 'queue'
    ctx = multiprocessing.get_context('spawn')
    with _drain_lock(path) as acquired:
        assert acquired
        recv, send = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=lock_probe, args=(path, send))
        try:
            proc.start()
            send.close()
            assert recv.poll(10)
            assert recv.recv() is False
            proc.join(5)
            assert proc.exitcode == 0
        finally:
            if proc.is_alive():
                proc.kill()
                proc.join(5)
            proc.close()
            recv.close()
            send.close()


def test_queue_connections_close_on_success_and_failure(service):
    runner, _, _, path = service
    with runner._db() as db:
        db.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    with pytest.raises(RuntimeError):
        with runner._db() as failed:
            failed.execute("UPDATE jobs SET status='pending'")
            raise RuntimeError('rollback')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        failed.execute('SELECT 1')
    path.rename(path.with_suffix('.moved'))


def test_process_context_selection(monkeypatch):
    from zeline import memory_gain_automation as automation
    import threading
    monkeypatch.setattr(automation.mp, 'get_context', lambda method: method)
    monkeypatch.setattr(automation, '_WINDOWS', True)
    assert automation._process_context(coordinator=True) == 'spawn'
    assert automation._process_context() == 'spawn'
    monkeypatch.setattr(automation, '_WINDOWS', False)
    monkeypatch.setattr(threading, 'active_count', lambda: 2)
    assert automation._process_context(coordinator=True) == 'forkserver'
    assert automation._process_context() == 'fork'
    monkeypatch.setattr(threading, 'active_count', lambda: 1)
    assert automation._process_context(coordinator=True) == 'fork'


def test_snapshot_connections_closed_before_worker_start(service, monkeypatch):
    from zeline import memory_gain_automation as automation
    runner, _, receipt, _ = service
    connect = sqlite3.connect
    opened = []
    def tracked_connect(*args, **kwargs):
        db = connect(*args, **kwargs)
        opened.append(db)
        return db
    monkeypatch.setattr(automation.sqlite3, 'connect', tracked_connect)
    def check_snapshots(*args, **kwargs):
        # Store connections may also be tracked; every connection must be closed.
        assert opened
        for db in opened:
            with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                db.execute('SELECT 1')
        raise RuntimeError('checked snapshots')
    monkeypatch.setattr(automation, 'evaluate_memory_gain', check_snapshots)
    with pytest.raises(RuntimeError, match='checked snapshots'):
        runner._evaluate('telegram:owner', receipt['record_id'], 1, 'journal-v1',
                         time.monotonic() + 10)


@pytest.mark.parametrize('coordinator', [False, True])
def test_start_failure_closes_pipes_and_process(service, monkeypatch, coordinator):
    from zeline import memory_gain_automation as automation
    runner, _, receipt, _ = service
    class Pipe:
        closed = False
        def close(self):
            self.closed = True
    recv, send = Pipe(), Pipe()
    class Process:
        pid = None
        closed = False
        def start(self):
            raise RuntimeError('start failed')
        def close(self):
            self.closed = True
    proc = Process()
    class Context:
        def Pipe(self, **kwargs):
            return recv, send
        def Process(self, **kwargs):
            return proc
    monkeypatch.setattr(automation, '_process_context', lambda **kwargs: Context())
    fn = runner._run if coordinator else runner._evaluate
    with pytest.raises(RuntimeError, match='start failed'):
        fn('telegram:owner', receipt['record_id'], 1, 'journal-v1', time.monotonic() + 10)
    assert recv.closed and send.closed and proc.closed


@pytest.mark.parametrize('coordinator', [False, True])
def test_real_spawn_evaluation(service, monkeypatch, coordinator):
    from zeline import memory_gain_automation as automation
    runner, _, receipt, _ = service
    runner.timeout = 5
    monkeypatch.setattr(automation, '_process_context',
                        lambda **kwargs: automation.mp.get_context('spawn'))
    fn = runner._run if coordinator else runner._evaluate
    result = fn('telegram:owner', receipt['record_id'], 1, 'journal-v1',
                time.monotonic() + 20)
    assert result['status'] == 'completed'
    assert result['report']['qualified']


def test_windows_lock_uses_same_byte_and_nonblocking_mode(tmp_path, monkeypatch):
    import errno
    import sys
    from types import SimpleNamespace
    from zeline import memory_gain_automation as automation
    calls = []
    held = set()
    def locking(fd, mode, count):
        calls.append((mode, count, os.lseek(fd, 0, os.SEEK_CUR)))
        assert os.fstat(fd).st_size >= 1
        if mode == 1:
            if held:
                raise OSError(errno.EACCES, 'locked')
            held.add(fd)
        else:
            held.remove(fd)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(
        locking=locking, LK_NBLCK=1, LK_UNLCK=2))
    monkeypatch.setattr(automation, '_WINDOWS', True)
    with automation._drain_lock(tmp_path / 'queue') as acquired:
        assert acquired
        with automation._drain_lock(tmp_path / 'queue') as contended:
            assert not contended
    assert calls == [(1, 1, 0), (1, 1, 0), (2, 1, 0)]
    assert not held


def test_windows_cleanup_terminates_process_tree(monkeypatch):
    import subprocess
    from zeline import memory_gain_automation as automation
    calls = []
    class Process:
        pid = 123
        alive = True
        def is_alive(self):
            return self.alive
        def terminate(self):
            calls.append('terminate')
            self.alive = False
        def join(self, timeout):
            calls.append('join')
        def close(self):
            calls.append('close')
    monkeypatch.setattr(automation, '_WINDOWS', True)
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs: calls.append(args))
    automation._stop_process(Process(), tree=True)
    assert calls == [['taskkill', '/PID', '123', '/T', '/F'], 'terminate', 'join', 'close']


def test_failure_diagnostics_never_include_exception_message(service, monkeypatch, capsys):
    runner, _, receipt, _ = service
    monkeypatch.setenv('ZELINE_CI_WORKER_DIAGNOSTICS', '1')
    def fail(*args, **kwargs):
        raise PermissionError('private-prompt-or-credential-value')
    monkeypatch.setattr(runner, '_run', fail)
    runner.enqueue('telegram:owner', receipt['record_id'], 1, 'journal-v1')
    assert runner.drain()[0]['error'] == 'PermissionError'
    stderr = capsys.readouterr().err
    assert 'PermissionError' in stderr and ':fail' in stderr
    assert 'private-prompt-or-credential-value' not in stderr


@pytest.mark.parametrize('alive', [False, True])
def test_posix_group_cleanup_permission_race_still_reaps_child(monkeypatch, alive):
    from zeline import memory_gain_automation as automation
    calls = []
    class Process:
        pid = 123
        def __init__(self):
            self.alive = alive
        def is_alive(self):
            return self.alive
        def terminate(self):
            calls.append('terminate')
            self.alive = False
        def join(self, timeout):
            calls.append('join')
        def close(self):
            calls.append('close')
    def denied(*args):
        raise PermissionError('process group unavailable')
    monkeypatch.setattr(automation, '_WINDOWS', False)
    monkeypatch.setattr(automation.os, 'killpg', denied, raising=False)
    automation._stop_process(Process(), tree=True)
    assert calls == (['terminate'] if alive else []) + ['join', 'close']


def network_worker(prompt, store, record_id, revision, mode):
    import socket
    socket.socket()
    return '', None
