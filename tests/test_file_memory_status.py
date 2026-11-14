"""Canonical note edits must invalidate derived-index status without writeback."""
import sqlite3

import pytest

from zeline import config, memory
from zeline.reflection_markdown import learning_status, record_markdown

IDENTITY = 'telegram:file-status'


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Agent fixtures reload package modules; bind this test to the same live
    # module graph as runtime-local imports instead of stale collection objects.
    import importlib
    global config, memory, learning_status, record_markdown
    config = importlib.import_module('zeline.config')
    memory = importlib.import_module('zeline.memory')
    reflection = importlib.import_module('zeline.reflection_markdown')
    learning_status, record_markdown = reflection.learning_status, reflection.record_markdown
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    monkeypatch.setattr(memory, 'LEGACY_FILE', tmp_path / 'legacy.json')
    return memory.MemoryStore(IDENTITY)


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def journal(store, text='Original canonical fact.'):
    receipt = store.add_receipt(text)
    record_markdown(IDENTITY, 'add_memory', {'fact': text}, receipt)
    return receipt


def test_manual_markdown_edit_invalidates_sqlite_active_revision_without_writeback(store):
    receipt = journal(store)
    assert learning_status(IDENTITY)['refinement']['active'] is True
    path = store.canonical_paths['MEMORY.md']
    path.write_text(path.read_text().replace('Original canonical fact.', 'Manually corrected fact.'))
    before = snapshot(config.DATA_DIR)
    status = learning_status(IDENTITY)['refinement']
    assert status['active'] is False
    assert status['active_records'] == 0
    assert status['stage'] in {'stale', 'unverified'}
    assert snapshot(config.DATA_DIR) == before
    with sqlite3.connect(store.db_path) as db:
        assert 'Original canonical fact.' in db.execute(
            'SELECT payload FROM memory_items WHERE owner=? AND id=?',
            (store.key, receipt['record_id'])).fetchone()[0]


def test_status_reads_legacy_index_without_initializing_or_migrating(store):
    # Build the supported legacy SQLite state directly: no canonical notes yet.
    config.DATA_DIR.joinpath('memory').mkdir()
    import json
    record = dict(id='legacy', revision=1, text='Legacy fact.', state='active',
                  expires_at=None, kind='fact', source='user', confidence=1,
                  created_at=1, updated_at=1)
    with sqlite3.connect(store.db_path) as db:
        db.execute('CREATE TABLE memory_items(owner TEXT,id TEXT,payload TEXT)')
        db.execute('CREATE TABLE memory_revisions(owner TEXT,id TEXT,revision INTEGER,payload TEXT)')
        db.execute('CREATE TABLE memory_retrievals(owner TEXT,record_id TEXT,revision INTEGER,assessment TEXT,turn_id TEXT,observed REAL)')
        db.execute('INSERT INTO memory_items VALUES(?,?,?)', (store.key, 'legacy', json.dumps(record)))
        db.execute('INSERT INTO memory_revisions VALUES(?,?,?,?)', (store.key, 'legacy', 1, json.dumps(record)))
    record_markdown(IDENTITY, 'add_memory', {}, dict(version=1, kind='memory', state='created',
                    record_id='legacy', revision=1, summary='Legacy fact.'))
    before = snapshot(config.DATA_DIR)
    status = learning_status(IDENTITY)['refinement']
    assert status['active'] is True
    assert snapshot(config.DATA_DIR) == before
    assert not next(iter(store.canonical_paths.values())).parent.exists()


def test_empty_status_does_not_create_storage(store):
    assert learning_status(IDENTITY)['refinement']['stage'] == 'empty'
    assert list(config.DATA_DIR.iterdir()) == []


def test_memory_journal_names_real_canonical_file(store):
    receipt = store.add_receipt('Canonical journal receipt.')
    # Exercise the journal contract independently of backend receipt rollout.
    receipt['file_path'] = f'memories/{store.key}/MEMORY.md'
    record_markdown(IDENTITY, 'add_memory', {}, receipt)
    path = config.DATA_DIR / 'learning' / store.key / 'Self-Refinement.md'
    assert f"  > File: memories/{store.key}/MEMORY.md\n" in path.read_text()
    assert receipt['file_path'] == f'memories/{store.key}/MEMORY.md'


@pytest.mark.parametrize('damage', ['corrupt', 'missing_one', 'missing_both', 'symlink', 'dangling', 'pending'])
def test_canonical_damage_never_reactivates_stale_index(store, damage):
    journal(store)
    note = store.canonical_paths['MEMORY.md']
    if damage == 'corrupt':
        note.write_text('# broken\n')
    elif damage == 'missing_one':
        note.unlink()
    elif damage == 'missing_both':
        for path in store.canonical_paths.values():
            path.unlink()
    elif damage == 'pending':
        note.with_name('.transaction.json').write_text('{}')
    else:
        target = config.DATA_DIR / 'target.md'
        if damage == 'symlink':
            target.write_bytes(note.read_bytes())
        note.unlink()
        note.symlink_to(target)
    before = snapshot(config.DATA_DIR)
    status = learning_status(IDENTITY)['refinement']
    assert status['active'] is False
    assert status['active_records'] == 0
    assert status['stage'] in {'unverified', 'stale'}
    assert snapshot(config.DATA_DIR) == before


@pytest.mark.parametrize('file_path', ['memories/' + '0' * 32 + '/MEMORY.md',
                                      '../MEMORY.md', 'memories/x/USER.md',
                                      'memories/{owner}/other.md'])
def test_memory_journal_does_not_echo_invalid_canonical_paths(store, file_path):
    receipt = store.add_receipt('Validated receipt path.')
    receipt['file_path'] = file_path.format(owner=store.key)
    path = record_markdown(IDENTITY, 'add_memory', {}, receipt)
    assert '  > File:' not in path.read_text()


def test_existing_legacy_journal_is_not_rewritten(store):
    receipt = journal(store)
    path = config.DATA_DIR / 'learning' / store.key / 'Self-Refinement.md'
    original = path.read_text()
    legacy = ''.join(line for line in original.splitlines(keepends=True) if not line.startswith('  > File:'))
    path.write_text(legacy)
    record_markdown(IDENTITY, 'add_memory', {}, receipt)
    assert path.read_text() == legacy
    assert learning_status(IDENTITY)['refinement']['active'] is True


def test_user_note_journal_names_user_file(store):
    receipt = store.add_receipt('User prefers concise responses.', kind='preference')
    receipt['file_path'] = f'memories/{store.key}/USER.md'
    path = record_markdown(IDENTITY, 'add_memory', {}, receipt)
    assert f'  > File: memories/{store.key}/USER.md\n' in path.read_text()
    assert learning_status(IDENTITY)['refinement']['active'] is True
    note = store.canonical_paths['USER.md']
    note.write_text(note.read_text().replace('User prefers concise responses.', 'User prefers detailed responses.'))
    assert learning_status(IDENTITY)['refinement']['active_records'] == 0


def test_status_never_initializes_memory_store(store, monkeypatch):
    journal(store)
    def forbidden(*args, **kwargs):
        raise AssertionError('status must not initialize memory stores')
    monkeypatch.setattr(memory.MemoryStore, '__init__', forbidden)
    assert learning_status(IDENTITY)['refinement']['active'] is True
