"""Bounded in-process runtime observations; no provider calls or task control."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from contextlib import closing, contextmanager
from functools import wraps

from zeline import config

MAX_AGENTS = 128
MAX_EVENTS = 200
MAX_CRON_BYTES = 262_144
MAX_JOBS = 64
STARTED = time.monotonic()


def agent_id(identity: str) -> str:
    return 'agent-' + hashlib.sha256(str(identity).encode()).hexdigest()[:12]


def safe_label(value, limit=160):
    text = str(value or '')[:4096]
    key = str(getattr(config, 'API_KEY', '') or '')
    if len(key) >= 5:
        text = text.replace(key, '[redacted]')
    text = re.sub(r'(?i)\b(?:bearer\s+|(?:api[_ -]?key|token|password|secret|authorization|private[_ -]?key)\s*[:=]\s*)[^\s,;]+', '[redacted]', text)
    text = re.sub(r'\b(?:sk-|ghp_|github_pat_|eyJ)[A-Za-z0-9_.-]{12,}', '[redacted]', text)
    text = re.sub(r'https?://[^\s]+', '[URL omitted]', text)
    return ' '.join(text.split())[:limit]


def gateway_name(identity):
    """Return platform provenance, never a raw account/session identifier."""
    prefix = str(identity).split(':', 1)[0]
    return prefix if ':' in str(identity) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', prefix) else 'unknown'


class RuntimeState:
    def __init__(self):
        self.enabled = False
        self._lock = threading.RLock()
        self._agents = OrderedDict()
        self._events = deque(maxlen=MAX_EVENTS)
        self._sequence = 0
        self._complete = True
        self._provider_health = None

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def event(self, key, kind, label, detail='', status='running', gateway=None):
        if not self.enabled:
            return
        with self._lock:
            self._sequence += 1
            self._events.append({'id': self._sequence, 'at': time.time(), 'agent_id': key,
                'kind': safe_label(kind, 40), 'label': safe_label(label, 80),
                'detail': safe_label(detail), 'status': status,
                'gateway': gateway or self._agents.get(key, {}).get('gateway', 'cron' if kind == 'cron' else 'runtime')})

    def begin(self, identity, task, model='', kind='main', parent=None):
        key, now = agent_id(identity), time.time()
        with self._lock:
            self._agents[key] = {'id': key, 'name': 'Subagent' if kind == 'subagent' else 'Main agent',
                'kind': kind, 'parent_id': agent_id(parent) if parent else None,
                'gateway': gateway_name(identity), 'session_id': agent_id(parent or identity),
                'state': 'Thinking', 'status': 'running', 'active': True,
                'task': safe_label(task), 'tool': '', 'model': safe_label(model, 80),
                'started_at': now, 'updated_at': now, 'ended_at': None}
            self._agents.move_to_end(key)
            while len(self._agents) > MAX_AGENTS:
                victim = next((k for k, row in self._agents.items() if not row['active']), next(iter(self._agents)))
                if self._agents[victim]['active']:
                    self._complete = False
                self._agents.pop(victim)
            self.event(key, 'agent', 'Task started', kind)
        return key

    def update(self, key, state, tool=None):
        with self._lock:
            row = self._agents.get(key)
            if row is None:
                return
            row['state'] = state
            row['updated_at'] = time.time()
            if tool is not None:
                row['tool'] = safe_label(tool, 80)

    def finish(self, key, status):
        with self._lock:
            row = self._agents.get(key)
            if row is None:
                return
            row.update(state='Waiting', status=status, active=False, ended_at=time.time(), updated_at=time.time())
            self.event(key, 'agent', 'Task ' + status, status=status)

    def provider(self, key, report):
        if not self.enabled or not isinstance(report, dict):
            return
        from zeline.provider_health import CATEGORIES, safe_http, category
        status = report.get('status')
        if status not in {'healthy', 'error'}:
            return
        code = safe_http(report.get('http_status'))
        reason = report.get('category')
        if code is not None and code >= 400:
            status = 'error'
            reason = reason if reason in CATEGORIES else category(code)
        reason = reason if reason in CATEGORIES else 'error'
        with self._lock:
            self._provider_health = {'status': status, 'category': reason if status == 'error' else None,
                                     'http_status': code, 'updated_at': time.time(), 'agent_id': key}
            self.event(key, 'provider', 'Provider response' if status == 'healthy' else 'Provider failed',
                       reason if status == 'error' else '', status=status)

    def snapshot(self):
        now = time.time()
        with self._lock:
            agents = [dict(row, elapsed_seconds=max(0, (row['ended_at'] or now) - row['started_at']))
                      for row in self._agents.values()]
            return {'agents': agents, 'events': [dict(row) for row in self._events], 'agents_complete': self._complete,
                    'provider_health': dict(self._provider_health) if self._provider_health else None}


STATE = RuntimeState()


class Trace:
    """Observe callbacks without changing their signatures or streaming policy."""
    def __init__(self, identity, task, model='', kind='main', parent=None):
        self.identity, self.task, self.model = identity, task, model
        self.kind, self.parent = kind, parent
        self.state = STATE
        self.key = None
        self.outcome = 'completed'

    def __enter__(self):
        if self.state.enabled:
            self.key = self.state.begin(self.identity, self.task, self.model, self.kind, self.parent)
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if self.key:
            self.state.finish(self.key, 'error' if exc_type else self.outcome)
        return False

    def _wrap(self, callback, observe):
        if not self.key:
            return callback
        def wrapped(*args, **kwargs):
            observe(*args, **kwargs)
            if callback:
                return callback(*args, **kwargs)
        return wraps(callback)(wrapped) if callback else wrapped

    @contextmanager
    def provider(self, agent):
        """Attach only within this send; no public callback signature changes."""
        if not self.key:
            yield
            return
        absent = object()
        previous = getattr(agent, '_on_provider', absent)
        def observe(report):
            self.state.provider(self.key, report)
        agent._on_provider = observe
        try:
            yield
        finally:
            if previous is absent:
                del agent._on_provider
            else:
                agent._on_provider = previous

    def tool(self, callback):
        def observe(name, _args):
            if name == 'ask_user':
                state = 'Waiting for Directions'
            elif name in {'run_shell', 'execute_code', 'process_control', 'delegate_task'}:
                state = 'Running'
            elif name.startswith(('read', 'search', 'list', 'recall', 'load')):
                state = 'Recalling'
            else:
                state = 'Executing'
            self.state.update(self.key, state, tool=name)
            self.state.event(self.key, 'tool', 'Tool started', name)
        return self._wrap(callback, observe)

    def result(self, callback):
        def observe(name, _args, result):
            failed = isinstance(result, str) and result.lstrip().startswith('ERROR')
            self.state.update(self.key, 'Reflecting')
            self.state.event(self.key, 'tool', 'Tool failed' if failed else 'Tool completed', name,
                             'error' if failed else 'completed')
        return self._wrap(callback, observe)

    def iteration(self, callback):
        return self._wrap(callback, lambda *_: self.state.update(self.key, 'Waiting for Provider Response'))

    def narration(self, callback):
        return self._wrap(callback, lambda *_: self.state.update(self.key, 'Working'))

    def stream(self, callback):
        # Supplying a callback where none existed can change provider behavior.
        if callback is None:
            return None
        return self._wrap(callback, lambda *_: self.state.update(self.key, 'Typing'))


def _cron_snapshot(cron):
    from zeline import scheduler
    path = scheduler.jobs_path()
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_CRON_BYTES + 1)
        if len(raw) > MAX_CRON_BYTES:
            return [], False
        jobs = json.loads(raw)
        if not isinstance(jobs, list):
            return [], False
    except FileNotFoundError:
        return [], True
    except (OSError, ValueError):
        return [], False
    running = set(cron.busy) if cron is not None else set()
    thread = getattr(cron, '_thread', None) if cron is not None else None
    armed = bool(thread is not None and thread.is_alive() and scheduler.enabled())
    output = []
    for job in jobs[:MAX_JOBS]:
        if not isinstance(job, dict):
            continue
        key = safe_label(job.get('id'), 64)
        enabled = job.get('enabled', True) is True
        last = safe_label(job.get('last_status'), 100)
        status = ('running' if key in running else 'disabled' if not enabled or not scheduler.enabled() else
                  'error' if last.startswith('error') else 'scheduled' if armed else 'not armed')
        output.append({'id': key, 'name': safe_label(job.get('prompt') or 'Script job', 100),
            'schedule': safe_label(job.get('schedule'), 80), 'enabled': enabled,
            'gateway': 'cron', 'origin_gateway': 'unknown', 'armed': enabled and armed,
            'next_run': job.get('next_run', 0), 'last_run': job.get('last_run', 0),
            'status': status, 'last_status': last})
    return output, len(jobs) <= MAX_JOBS


def _durable_agents():
    path = config.DATA_DIR / 'jobs' / 'jobs.sqlite3'
    if not path.is_file():
        return [], True
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            deadline = time.monotonic() + .04
            db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            rows = db.execute("SELECT id,owner,state,created_at,updated_at,lease_until,substr(spec,1,2048) "
                              "FROM jobs WHERE state IN ('queued','running') ORDER BY updated_at DESC LIMIT 65").fetchall()
    except sqlite3.Error:
        return [], False
    output = []
    now = time.time()
    for key, owner, state, created, updated, lease, spec in rows[:MAX_JOBS]:
        active = state == 'running' and (lease or 0) > now
        try:
            task = json.loads(spec).get('goal', 'Durable task')
        except (ValueError, AttributeError):
            task = 'Durable task'
        output.append({'id': 'job-' + key, 'name': 'Durable subagent', 'kind': 'subagent',
            'parent_id': 'agent-' + owner[:12], 'state': 'Running' if active else 'Waiting',
            'gateway': 'unknown', 'session_id': 'agent-' + owner[:12],
            'status': 'running' if active else 'queued' if state == 'queued' else 'lease expired',
            'active': active, 'task': safe_label(task), 'tool': 'durable worker',
            'started_at': None, 'updated_at': updated, 'elapsed_seconds': None,
            'timing_note': 'Execution start time is unavailable; timestamps are not fabricated.'})
    return output, len(rows) <= MAX_JOBS


def _access_path():
    return config.DATA_DIR / 'run' / 'runtime-access.json'


def _write_access(dashboard):
    """Expose the private local entrypoint to owner CLI tooling, not public HTML."""
    import tempfile
    path = _access_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError('Unsafe runtime access path')
    fd, temporary = tempfile.mkstemp(prefix='.runtime-', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fd = None
            json.dump({'pid': os.getpid(), 'url': dashboard.url, 'owner_url': dashboard.owner_url()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def start_dashboard(sessions, runtime, cron, *, provider=''):
    """Join the existing gateway lifecycle; never replace another listener."""
    from zeline.runtime_dashboard import RuntimeDashboard
    if os.environ.get('ZELINE_RUNTIME_ENABLED', '1').lower() in {'0', 'false', 'off'}:
        return None
    dashboard = None
    try:
        port = int(os.environ.get('ZELINE_RUNTIME_PORT', '8765'))
        if not 0 <= port <= 65535:
            raise ValueError('Invalid local port')
        dashboard = RuntimeDashboard(lambda: gateway_snapshot(runtime, cron, provider=provider,
            sessions=sessions), port=port)
        try:
            dashboard.start()
        except OSError:
            if port == 0:
                raise
            dashboard.stop()
            dashboard = RuntimeDashboard(lambda: gateway_snapshot(runtime, cron, provider=provider,
                sessions=sessions), port=0)
            dashboard.start()
        _write_access(dashboard)
        sessions._runtime_dashboard = dashboard
        STATE.enable()
        STATE.event('', 'gateway', 'Runtime dashboard started', 'Local read-only observer')
        return dashboard
    except (OSError, ValueError):
        if dashboard is not None:
            dashboard.stop()
        sessions._runtime_dashboard = None
        STATE.disable()
        return None


def stop_dashboard(sessions):
    dashboard = getattr(sessions, '_runtime_dashboard', None)
    if dashboard is not None:
        owner_url = dashboard.owner_url()
        dashboard.stop()
        path = _access_path()
        try:
            if not path.is_symlink() and json.loads(path.read_text()).get('owner_url') == owner_url:
                path.unlink()
        except (OSError, ValueError):
            pass
        sessions._runtime_dashboard = None
    STATE.disable()


def gateway_snapshot(runtime=None, cron=None, *, provider=None, sessions=None):
    now = time.time()
    data = STATE.snapshot()
    durable, durable_complete = _durable_agents()
    parents = {row['id']: row for row in data['agents']}
    for job in durable:
        parent = parents.get(job['parent_id'])
        if parent:
            job['gateway'] = parent.get('gateway', 'unknown')
    data['agents'].extend(durable)
    jobs, cron_available = _cron_snapshot(cron)
    from zeline.gateway_observer import adapter_snapshot
    adapters = adapter_snapshot(runtime, config.GATEWAYS)
    stopped = bool(runtime is not None and runtime.stop_event.is_set())
    draining = bool(sessions is not None and sessions._paused.is_set())
    return {'generated_at': now, 'gateway': {'state': 'unknown' if runtime is None else 'stopped' if stopped else 'draining' if draining else 'running',
        'uptime_seconds': max(0, time.monotonic() - STARTED), 'pid': os.getpid(),
        'provider': safe_label(provider or getattr(config, 'PROTOCOL', 'configured'), 80),
        'model': safe_label(config.MODEL, 100), 'provider_health': data['provider_health'],
        'adapters': adapters},
        'agents': data['agents'], 'cron': jobs, 'events': data['events'],
        'metrics': {'active_agents': sum(bool(row.get('active')) for row in data['agents']) if STATE.enabled and durable_complete and data['agents_complete'] else None,
            'running_cron': sum(row['status'] == 'running' for row in jobs) if cron_available and cron is not None else None,
            'scheduled_cron': sum(bool(row.get('armed')) for row in jobs) if cron_available and cron is not None else None, 'events': len(data['events'])},
        'sources': {'cron_complete': cron_available, 'durable_complete': durable_complete,
                    'agents_complete': data['agents_complete'], 'cron_execution_available': cron is not None,
                    'observer_active': STATE.enabled,
                    'durable_timing': 'unknown start time'}}
