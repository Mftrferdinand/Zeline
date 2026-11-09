"""Bounded, credential-free observations attached to one gateway runtime."""
from collections import OrderedDict
import re
import threading
import time

_DETAILS = {
    'poll_ok': 'Telegram polling API verified',
    'wa_ready': 'WhatsApp authenticated socket open reported by local bridge',
    'wa_listening': 'Local bridge HTTP listener ready; WhatsApp login is unknown',
    'ready': 'Discord session handshake verified',
    'listening': 'Local webhook listener is bound',
    'bridge_ok': 'Bridge reachable; WhatsApp login status is unknown',
    'connecting': 'Waiting for protocol evidence',
    'starting': 'Adapter worker started; no connection evidence yet',
    'error': 'Adapter failed; inspect private gateway logs',
    'stopped': 'Adapter worker stopped',
    'reconnecting': 'Connection failed; retrying',
}
_STATES = {'starting', 'connecting', 'connected', 'listening', 'bridge reachable', 'reconnecting', 'error', 'stopped'}
_LOCK = threading.Lock()


class AdapterObserver:
    def __init__(self):
        self._lock = threading.Lock()
        self._rows = OrderedDict()

    def record(self, name, state, detail=''):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', str(name)):
            return
        state = state if state in _STATES else 'error'
        with self._lock:
            self._rows[name] = {'state': state, 'updated_at': time.time(), 'detail': _DETAILS.get(detail, _DETAILS.get(state, 'No protocol evidence available'))}
            self._rows.move_to_end(name)
            while len(self._rows) > 128:
                self._rows.popitem(last=False)

    def snapshot(self):
        with self._lock:
            return {name: dict(row) for name, row in self._rows.items()}


def report(stop_event, name, state, detail=''):
    with _LOCK:
        observer = getattr(stop_event, '_zeline_observer', None)
        if observer is None:
            observer = AdapterObserver()
            stop_event._zeline_observer = observer
    observer.record(name, state, detail)


def adapter_snapshot(runtime, cfg):
    from .gateways import GATEWAYS
    event = getattr(runtime, 'stop_event', None)
    observer = getattr(event, '_zeline_observer', None)
    observations = observer.snapshot() if observer is not None else {}
    alive = {name for name, thread in getattr(runtime, 'threads', []) if thread.is_alive()}
    selected_names = getattr(event, '_zeline_selected', None)
    rows = []
    for name in sorted(set(GATEWAYS) | set(cfg))[:128]:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}', str(name)):
            continue
        enabled = bool(cfg.get(name, {}).get('enabled', False))
        selected = bool(enabled and (selected_names is None or name in selected_names))
        row = {'name': name, 'state': 'unknown', 'enabled': enabled, 'selected': selected,
               'thread_alive': name in alive, 'updated_at': None, 'detail': 'No protocol evidence available'}
        if name in observations:
            row.update(observations[name])
        if not enabled:
            row.update(state='disabled', detail='Disabled by configuration', updated_at=None)
        elif name not in GATEWAYS:
            row.update(state='unavailable', detail='No registered adapter implementation', updated_at=None)
        elif not selected:
            row.update(state='not selected', detail='Not selected for this runtime', updated_at=None)
        elif name not in alive and row['state'] != 'error':
            row.update(state='stopped' if runtime is not None else 'unknown', detail='No live adapter worker')
        rows.append(row)
    return rows
