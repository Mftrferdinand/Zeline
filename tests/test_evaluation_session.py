"""A coordinator may already own its session on a POSIX process launcher."""
from types import SimpleNamespace


def test_coordinator_accepts_existing_owned_session(monkeypatch, tmp_path):
    from zeline import config, memory, memory_gain_automation as automation
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    monkeypatch.setattr(automation, '_WINDOWS', False)
    calls = []

    def setsid():
        raise PermissionError('already a session leader')

    monkeypatch.setattr(automation, 'os', SimpleNamespace(
        setsid=setsid, getpid=lambda: 123, getpgrp=lambda: 123,
        getsid=lambda pid: 123))
    connection = SimpleNamespace(send=calls.append, close=lambda: None)
    runner = SimpleNamespace(_evaluate=lambda *args: {'status': 'completed'})
    automation._coordinator(connection, runner, 'fixture', 'm1', 1, 'pack', 1,
                            tmp_path, tmp_path / 'memory')
    assert calls == [{'status': 'completed'}]


def test_coordinator_rejects_unowned_session(monkeypatch, tmp_path):
    from zeline import memory_gain_automation as automation
    monkeypatch.setattr(automation, '_WINDOWS', False)
    calls = []

    def setsid():
        raise PermissionError('session not available')

    monkeypatch.setattr(automation, 'os', SimpleNamespace(
        setsid=setsid, getpid=lambda: 123, getpgrp=lambda: 456,
        getsid=lambda pid: 456))
    connection = SimpleNamespace(send=calls.append, close=lambda: None)
    runner = SimpleNamespace(_evaluate=lambda *args: calls.append('unexpected evaluation'))
    automation._coordinator(connection, runner, 'fixture', 'm1', 1, 'pack', 1,
                            tmp_path, tmp_path / 'memory')
    assert calls == [{'status': 'failed', 'error': 'PermissionError'}]
