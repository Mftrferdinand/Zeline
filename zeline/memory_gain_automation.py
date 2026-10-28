"""Host-only bounded paired evaluation. Never expose registration to model tools.

Register fixed, reviewed case packs at host startup; enqueue after a correction receipt,
then drain from a host scheduler. Workers are trusted Python callables, not commands.
The per-process socket guard is defense-in-depth, not an OS network sandbox; for
untrusted worker code use an external OS sandbox with network namespace isolation.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import errno
import hashlib
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from .memory_gain_checker import evaluate_memory_gain


_WINDOWS = os.name == 'nt'


@contextmanager
def _drain_lock(path):
    """Lock byte zero on Windows, or the file on POSIX; never wait."""
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        if _WINDOWS:
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        try:
            if acquired:
                if _WINDOWS:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _stop_process(proc, *, tree=False):
    """Reap a started process, including coordinator descendants on cancellation."""
    if proc.pid is None:
        proc.close()
        return
    try:
        if tree and _WINDOWS:
            # taskkill /T also terminates spawned model workers, not just the parent.
            import subprocess
            try:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        elif tree and hasattr(os, 'killpg'):
            import signal
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if proc.is_alive():
            proc.terminate()
        proc.join(timeout=0.5)
        if proc.is_alive():
            if tree and not _WINDOWS and hasattr(os, 'killpg'):
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.kill()
            proc.join(timeout=0.5)
    finally:
        if not proc.is_alive():
            proc.close()


def _process_context(*, coordinator=False):
    import threading
    method = ('spawn' if _WINDOWS else
              'forkserver' if coordinator and threading.active_count() > 1 else 'fork')
    return mp.get_context(method)


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    expected: str
    split: str


def llm_answer(prompt, store, record_id, revision, mode):
    """Trusted model-only worker: fresh context, scoped recall, no tool execution."""
    import uuid
    from .agent import Zeline
    turn = 'eval-' + uuid.uuid4().hex
    selected = store.recall(prompt)
    store.record_retrieval(turn, selected['records'])
    # The baseline child has the target fact removed before this runs. Preserve
    # all other memory to isolate only the effect of the correction under test.
    context = selected.get('context') or '(none)'
    agent = Zeline(identity=store.identity, tool_profile='safe')
    agent.messages.append({'role': 'user', 'content':
        'Answer the question concisely using only available context. '
        'If unknown, answer "unknown".\n'
        f'Available memory:\n{context}\nQuestion: {prompt}'})
    reply = agent._call_llm(use_tools=False, force_stream=False)
    answer = reply.get('content')
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('Model returned no auditable text')
    return answer.strip(), turn


def _validate(cases):
    frozen = tuple(Case(*(getattr(c, field, None) for field in ('id', 'prompt', 'expected', 'split')))
                   for c in cases)
    ids, prompts, splits = set(), set(), set()
    if not 2 <= len(frozen) <= 64:
        raise ValueError('Invalid bounded case pack')
    for c in frozen:
        if (not isinstance(c.id, str) or not re.fullmatch(r'[\w.:-]{1,80}', c.id, re.ASCII)
                or not isinstance(c.prompt, str) or not 1 <= len(c.prompt) <= 1000
                or not isinstance(c.expected, str) or not 1 <= len(c.expected) <= 1000
                or c.split not in ('train', 'heldout') or c.id in ids
                or ' '.join(c.prompt.casefold().split()) in prompts):
            raise ValueError('Invalid host-owned cases')
        ids.add(c.id)
        prompts.add(' '.join(c.prompt.casefold().split()))
        splits.add(c.split)
    if splits != {'train', 'heldout'}:
        raise ValueError('Both train and heldout required')
    return frozen


def _child(conn, storage, snapshot, owner, record_id, revision, mode, prompt, worker, allow_network):
    try:
        import socket
        from . import config, memory
        os.environ['ZELINE_HOME'] = str(storage)
        config.DATA_DIR = storage
        os.environ['HOME'] = str(storage)
        os.environ['TMPDIR'] = str(storage)
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            os.environ.pop(key, None)
        if not allow_network:
            def denied(*args, **kwargs):
                raise PermissionError('network disabled for evaluation worker')
            socket.socket = denied
            socket.create_connection = denied
            socket.getaddrinfo = denied
        memory.MEMORY_DIR = storage / 'memory'
        memory.MEMORY_DIR.mkdir(mode=0o700)
        shutil.copyfile(snapshot, memory.MEMORY_DIR / 'memory.sqlite3')
        module, name = worker.split(':')
        fn = getattr(importlib.import_module(module), name)
        store = memory.MemoryStore(owner)
        answer, turn = fn(prompt, store, record_id, revision, mode)
        if not isinstance(answer, str) or len(answer) > 10000:
            raise ValueError('Invalid worker answer')
        if mode == 'candidate':
            if not isinstance(turn, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', turn):
                raise ValueError('Invalid candidate turn')
            retrieved = any(r['record_id'] == record_id and r['revision'] == revision
                            for r in store.retrievals(turn))
            if not retrieved:
                raise ValueError('Candidate turn lacks targeted retrieval')
        conn.send({'answer': answer, 'turn': turn, 'pid': os.getpid(),
                   'storage': str(storage), 'retrieved': mode == 'candidate'})
    except BaseException as exc:
        try:
            code = ('network disabled' if isinstance(exc, PermissionError)
                    and str(exc) == 'network disabled for evaluation worker'
                    else type(exc).__name__)
            conn.send({'error': code})
        except Exception:
            pass
    finally:
        conn.close()


def _coordinator(conn, automation, owner, record_id, revision, pack, deadline,
                 data_dir, memory_dir):
    try:
        if not _WINDOWS and hasattr(os, 'setsid'):
            try:
                os.setsid()  # Own process group so cancellation also stops model children.
            except PermissionError:
                # Some launchers already establish a session. Only accept EPERM
                # when both session and group are demonstrably owned by this child.
                if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
                    raise
        from . import config, memory
        config.DATA_DIR = Path(data_dir)
        memory.MEMORY_DIR = Path(memory_dir)
        conn.send(automation._evaluate(owner, record_id, revision, pack, deadline))
    except BaseException as exc:
        if getattr(os, 'environ', {}).get('ZELINE_CI_WORKER_DIAGNOSTICS') == '1':
            import sys
            import traceback
            frames = traceback.extract_tb(exc.__traceback__)[-8:]
            print('evaluation coordinator failure ' + type(exc).__name__ + ': ' +
                  ' -> '.join(f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
                              for frame in frames), file=sys.stderr)
        conn.send({'status': 'failed', 'error': 'network disabled' if str(exc) == 'network disabled'
                   else type(exc).__name__})
    finally:
        conn.close()


class MemoryGainAutomation:
    """Persistent queue and synchronous bounded host scheduler (one drain at a time).

    ``store_factory(owner)`` must construct the owner MemoryStore. Host must call
    register_pack before enqueue/drain on each startup; no cases stored in queue.
    This service never writes the owner's memory database, even on qualification:
    the checker assessment lives in isolated evaluation storage only.
    """
    def __init__(self, path, *, store_factory, timeout=10.0, max_pending=32,
                 allow_network=False):
        self.path = Path(path)
        if not 0 < timeout <= 120 or not 1 <= max_pending <= 1024:
            raise ValueError('Invalid evaluation bounds')
        self.timeout, self.max_pending, self.allow_network = timeout, max_pending, allow_network
        self.store_factory = store_factory
        self.packs = {}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (owner TEXT NOT NULL, record_id TEXT NOT NULL, '
                       'revision INTEGER NOT NULL, pack TEXT NOT NULL, digest TEXT NOT NULL, '
                       'status TEXT NOT NULL, created REAL NOT NULL, result TEXT, PRIMARY KEY(owner,record_id,revision,pack))')

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=0.2)) as db:
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
            db.execute('PRAGMA busy_timeout=200')
            with db:
                yield db

    def register_pack(self, name, cases, *, worker):
        if not isinstance(name, str) or not re.fullmatch(r'[\w.:-]{1,80}', name, re.ASCII):
            raise ValueError('Invalid pack name')
        if not isinstance(worker, str) or not re.fullmatch(
                r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', worker, re.ASCII):
            raise ValueError('Invalid trusted worker import path')
        frozen = _validate(cases)
        digest = hashlib.sha256(json.dumps([c.__dict__ for c in frozen],
                              sort_keys=True).encode()).hexdigest()
        self.packs[name] = frozen, worker, digest

    def enqueue(self, owner, record_id, revision, pack, *, allow_network=None):
        if allow_network is not None:
            raise ValueError('network policy is host-only at service construction')
        if pack not in self.packs:
            raise ValueError('Only registered host-owned case packs may be scheduled')
        if not isinstance(owner, str) or not owner or len(owner) > 256 or not isinstance(record_id, str) or not record_id:
            raise ValueError('Invalid owner or record')
        if type(revision) is not int or revision < 1:
            raise ValueError('Invalid revision')
        store = self.store_factory(owner)
        if not any(r['id'] == record_id and r['revision'] == revision
                   for r in store.records()):
            raise ValueError('Record revision is not current and live')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT digest FROM jobs WHERE owner=? AND record_id=? AND revision=? AND pack=?',
                                  (owner, record_id, revision, pack)).fetchone()
            if existing:
                if existing[0] != self.packs[pack][2]:
                    raise ValueError('Registered pack changed for existing job')
                return (owner, record_id, revision, pack)
            if db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0] >= self.max_pending:
                raise ValueError('Pending evaluation queue full')
            db.execute('INSERT INTO jobs (owner,record_id,revision,pack,digest,status,created) VALUES (?,?,?,?,?,?,?)',
                       (owner, record_id, revision, pack, self.packs[pack][2], 'pending', time.time()))
        return (owner, record_id, revision, pack)

    def recover_pending(self):
        """Host startup hook after confirming no old drain process is alive.

        Never call concurrently with a drain: a running child could otherwise
        duplicate a job. This is explicitly host-controlled crash recovery.
        """
        with _drain_lock(self.path) as acquired:
            if not acquired:
                raise RuntimeError('Evaluation drain still active')
            with self._db() as db:
                cursor = db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
                return cursor.rowcount

    def pending(self):
        with self._db() as db:
            return db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0]

    def _run(self, owner, record_id, revision, pack, deadline, stop_event=None):
        """Hard deadline for the entire job, including checker/storage operations."""
        from . import config, memory
        # Gateway scheduler runs in a thread: forkserver prevents inheriting
        # arbitrary thread locks into a model subprocess.
        ctx = _process_context(coordinator=True)
        recv, send = ctx.Pipe(duplex=False)
        proc = None
        try:
            proc = ctx.Process(target=_coordinator, args=(send, self, owner, record_id,
                                                      revision, pack, deadline,
                                                      config.DATA_DIR, memory.MEMORY_DIR))
            proc.start()
            send.close()
            while True:
                if stop_event is not None and stop_event.is_set():
                    raise TimeoutError('Evaluation cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Evaluation timeout')
                if recv.poll(min(0.2, remaining)):
                    return recv.recv()
        finally:
            try:
                if proc is not None:
                    _stop_process(proc, tree=True)
            finally:
                send.close()
                recv.close()

    def _evaluate(self, owner, record_id, revision, pack, deadline):
        cases, worker, digest = self.packs[pack]
        store = self.store_factory(owner)
        if not any(r['id'] == record_id and r['revision'] == revision for r in store.records()):
            raise ValueError('Stale record revision')
        workers = []
        with tempfile.TemporaryDirectory(prefix='memory-gain-') as root:
            root = Path(root)
            snapshot = root / 'snapshot.sqlite3'
            with closing(sqlite3.connect(store.db_path.resolve().as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            baseline_snapshot = root / 'baseline.sqlite3'
            shutil.copyfile(snapshot, baseline_snapshot)
            # The baseline retains the owner's other memory but cannot see the
            # exact correction under test, including historical revisions.
            with closing(sqlite3.connect(baseline_snapshot)) as baseline_db, baseline_db:
                for table, column in (('memory_items', 'id'),
                                      ('memory_revisions', 'id'),
                                      ('memory_retrievals', 'record_id')):
                    baseline_db.execute(f'DELETE FROM {table} WHERE owner=? AND {column}=?',
                                        (store.key, record_id))
            eval_dir = root / 'evaluation'
            eval_dir.mkdir()
            from . import memory
            old_dir = memory.MEMORY_DIR
            try:
                memory.MEMORY_DIR = eval_dir
                shutil.copyfile(snapshot, eval_dir / 'memory.sqlite3')
                evaluation = self.store_factory(owner)
                ctx = _process_context()
                def invoke(mode, prompt):
                    storage = root / f'worker-{len(workers)}'
                    storage.mkdir(mode=0o700)
                    recv, send = ctx.Pipe(duplex=False)
                    proc = None
                    try:
                        proc = ctx.Process(target=_child, args=(send, storage,
                                                              baseline_snapshot if mode == 'baseline' else snapshot,
                                                              owner, record_id, revision, mode, prompt,
                                                              worker, self.allow_network))
                        proc.start()
                        send.close()
                        remaining = min(self.timeout, deadline - time.monotonic())
                        if remaining <= 0 or not recv.poll(remaining):
                            raise TimeoutError('Worker timeout')
                        result = recv.recv()
                        if 'error' in result:
                            raise RuntimeError(result['error'])
                        workers.append({'pid': result['pid'], 'storage': result['storage']})
                        if mode == 'candidate':
                            selected = [r for r in evaluation.records() if r['id'] == record_id
                                        and r['revision'] == revision]
                            evaluation.record_retrieval(result['turn'], selected)
                            return result['answer'], result['turn']
                        return result['answer']
                    finally:
                        try:
                            if proc is not None:
                                _stop_process(proc)
                        finally:
                            send.close()
                            recv.close()
                report = evaluate_memory_gain(evaluation, record_id, revision,
                    cases=cases, checker=pack, baseline_runner=lambda p: invoke('baseline', p),
                    candidate_runner=lambda p: invoke('candidate', p))
                return {'status': 'completed', 'report': report, 'workers': workers}
            finally:
                memory.MEMORY_DIR = old_dir

    def drain(self, *, max_jobs=1, max_seconds=30.0, stop_event=None):
        if type(max_jobs) is not int or not 0 <= max_jobs <= 32 or not 0 < max_seconds <= 3600:
            raise ValueError('Invalid drain bounds')
        deadline = time.monotonic() + max_seconds
        results = []
        # Host-local nonblocking lock: at most one active drain across processes.
        with _drain_lock(self.path) as acquired:
            if not acquired:
                return []
            return self._drain_locked(max_jobs, deadline, results, stop_event)

    def _drain_locked(self, max_jobs, deadline, results, stop_event):
        for _ in range(max_jobs):
            if time.monotonic() >= deadline or (stop_event is not None and stop_event.is_set()):
                break
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT owner,record_id,revision,pack,digest FROM jobs WHERE status='pending' "
                                 'ORDER BY created,rowid LIMIT 1').fetchone()
                if not row:
                    break
                db.execute("UPDATE jobs SET status='running' WHERE owner=? AND record_id=? AND revision=? AND pack=?", row[:4])
            owner, record_id, revision, pack, digest = row
            try:
                if pack not in self.packs or self.packs[pack][2] != digest:
                    raise ValueError('Registered case pack missing or changed')
                result = self._run(owner, record_id, revision, pack, deadline, stop_event)
            except Exception as exc:
                if getattr(os, 'environ', {}).get('ZELINE_CI_WORKER_DIAGNOSTICS') == '1':
                    # Frame names/line numbers only: never emit prompts, owner data,
                    # exception messages, locals, or credentials into public CI logs.
                    import sys
                    import traceback
                    frames = traceback.extract_tb(exc.__traceback__)[-8:]
                    print('evaluation failure ' + type(exc).__name__ + ': ' +
                          ' -> '.join(f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
                                      for frame in frames), file=sys.stderr)
                result = {'status': 'failed', 'error': type(exc).__name__}
            with self._db() as db:
                db.execute('UPDATE jobs SET status=?, result=? WHERE owner=? AND record_id=? AND revision=? AND pack=?',
                           (result['status'], json.dumps(result, ensure_ascii=False), owner, record_id, revision, pack))
            results.append({'job': row[:4], **result})
        return results

    def result(self, owner, record_id, revision, pack):
        """Read a durable completed/failed receipt; never contains raw prompts or answers."""
        with self._db() as db:
            row = db.execute('SELECT result FROM jobs WHERE owner=? AND record_id=? AND revision=? AND pack=?',
                             (owner, record_id, revision, pack)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
