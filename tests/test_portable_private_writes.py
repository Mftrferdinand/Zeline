"""Private projections work without POSIX-only APIs and release SQLite handles."""
import json
import sqlite3

import pytest


def test_followup_save_without_fchmod(tmp_path, monkeypatch):
    from zeline import followups
    monkeypatch.setattr(followups, 'registry_path', lambda: tmp_path / 'owner.json')
    monkeypatch.delattr(followups.os, 'fchmod', raising=False)
    followups._save({'watches': []})
    assert json.loads((tmp_path / 'owner.json').read_text()) == {'watches': []}


def test_reflection_write_without_fchmod(tmp_path, monkeypatch):
    from zeline import config, reflection_markdown as journal
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.delattr(journal.os, 'fchmod', raising=False)
    receipt = {'version': 1, 'kind': 'memory', 'state': 'created', 'record_id': 'm1',
               'revision': 1, 'success': True, 'summary': 'Journal fixture'}
    path = journal.record_markdown('telegram:fixture', 'add_memory', {'fact': 'Journal fixture'}, receipt)
    assert path and path.is_file()


def test_windows_journal_lock_has_a_lockable_byte(tmp_path, monkeypatch):
    import os
    import sys
    from types import SimpleNamespace
    from zeline import reflection_markdown as journal
    locked = []

    def locking(fd, mode, count):
        assert os.fstat(fd).st_size >= count
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        locked.append(mode)

    windows_os = SimpleNamespace(name='nt', open=os.open, fdopen=os.fdopen,
                                O_CREAT=os.O_CREAT, O_RDWR=os.O_RDWR)
    monkeypatch.setattr(journal, 'os', windows_os)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(
        locking=locking, LK_LOCK=1, LK_UNLCK=0))
    with journal._file_lock(tmp_path / '.lock'):
        assert locked == [1]
    assert locked == [1, 0]


def test_status_closes_readonly_connection(tmp_path, monkeypatch):
    from zeline import config, reflection_markdown as journal
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    directory = tmp_path / 'skillforge'
    directory.mkdir()
    path = directory / 'skillforge.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE proposals (id,name,candidate,status,checks)')
        db.execute('INSERT INTO proposals VALUES (?,?,?,?,?)',
                   ('forge_fixture', 'fixture', 'rev', 'pending', '{}'))
    db.close()
    real_connect = sqlite3.connect
    connections = []

    def tracked(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(journal.sqlite3, 'connect', tracked)
    journal._learning_status_entry('telegram:fixture', {
        'enhancement': ('forge_fixture', 'rev', 'proposal, not active', '  > Skill: fixture')})
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')
