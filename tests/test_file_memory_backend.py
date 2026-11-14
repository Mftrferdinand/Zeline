"""Canonical Markdown integration contracts; all storage is temporary."""
import json
import sqlite3
import pytest
from zeline import memory


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('ZELINE_HOME', str(tmp_path))
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    monkeypatch.setattr(memory, 'LEGACY_FILE', tmp_path / 'memory.json')
    return memory.MemoryStore('test:owner')


def test_canonical_visible_body_is_primary_and_manually_reloadable(store):
    receipt = store.add_receipt('Project uses violet notebooks')
    paths = store.canonical_paths
    assert set(paths) == {'MEMORY.md', 'USER.md'}
    note = paths['MEMORY.md']
    assert 'Project uses violet notebooks' in note.read_text()
    version = store.retrieval_version()
    note.write_text(note.read_text().replace('violet notebooks', 'green notebooks'))
    record = store.records()[0]
    assert record['text'] == 'Project uses green notebooks'
    assert record['id'] == receipt['record_id']
    assert record['revision'] == 2
    assert store.retrieval_version() != version
    store.record_retrieval('manual', [record])
    assert store.retrievals('manual')[0]['revision'] == 2
    store.db_path.unlink()
    assert store.list() == ['Project uses green notebooks']
    assert store.records()[0]['revision'] == 2


def test_empty_read_creates_no_storage(store):
    assert store.list() == []
    assert not store.db_path.exists()
    assert not store.canonical_paths['MEMORY.md'].exists()
    assert not store.canonical_paths['USER.md'].exists()
    assert not store.db_path.parent.exists()


def test_version_observes_edit_before_index_reload(store):
    store.add('old topic')
    version = store.retrieval_version()
    p = store.canonical_paths['MEMORY.md']
    p.write_text(p.read_text().replace('old topic', 'new topic'))
    assert store.retrieval_version() != version


def test_sqlite_migration_is_backed_up_and_preserves_all_records(store):
    store.db_path.parent.mkdir(parents=True)
    record = memory._coerce_record({'text': 'legacy text', 'id': 'legacy', 'revision': 7, 'state': 'deleted'})
    with store._indexed_transaction() as (db, records):
        store._save(db, [record])
    assert store.records(include_inactive=True) == [record]
    backups = list(store.db_path.parent.glob('memory.sqlite3.*.bak'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert json.loads(db.execute('SELECT payload FROM memory_items').fetchone()[0]) == record
    assert store.canonical_paths['MEMORY.md'].exists()


@pytest.mark.parametrize('component', ['notes', 'identity', 'MEMORY.md', 'db', 'lock', 'journal'])
def test_symlink_components_fail_closed(store, tmp_path, component):
    external = tmp_path / 'external'
    external.mkdir()
    paths = store.canonical_paths
    directory = paths['MEMORY.md'].parent
    if component == 'notes':
        directory.parent.symlink_to(external, target_is_directory=True)
    elif component == 'identity':
        directory.parent.mkdir()
        directory.symlink_to(external, target_is_directory=True)
    else:
        directory.mkdir(parents=True)
        if component == 'db':
            store.db_path.parent.mkdir()
            target = store.db_path
        elif component == 'lock':
            target = directory / '.lock'
        elif component == 'journal':
            target = directory / '.transaction.json'
        else:
            target = paths[component]
        target.symlink_to(external / 'sentinel')
    result = store.add_receipt('must not write')
    assert result['state'] != 'created'
    assert list(external.iterdir()) == []


def test_corrupt_markdown_never_falls_back(store):
    store.add('saved text')
    path = store.canonical_paths['MEMORY.md']
    path.write_text('corrupt manual note')
    assert store.add_receipt('danger')['state'] != 'created'
    assert path.read_text() == 'corrupt manual note'
    with pytest.raises(ValueError):
        store.records()


def test_pair_failure_rolls_back_and_recovers(store, monkeypatch):
    from zeline import memory_markdown as md
    store.add('before')
    before = {n: p.read_bytes() for n, p in store.canonical_paths.items()}
    original = md.atomic_write if hasattr(md, 'atomic_write') else None
    assert original is not None, 'pair commit needs atomic readback write primitive'
    calls = []
    def fail_second(path, text):
        if path.name in {'MEMORY.md', 'USER.md'}:
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError('injected second note failure')
        return original(path, text)
    monkeypatch.setattr(md, 'atomic_write', fail_second)
    result = store.apply([{'action': 'add', 'text': 'after'},
                          {'action': 'add', 'text': 'explicit profile', 'kind': 'profile'}])
    assert result['success'] is False
    monkeypatch.setattr(md, 'atomic_write', original)
    assert store.list() == ['before']
    assert {n: p.read_bytes() for n, p in store.canonical_paths.items()} == before


def test_legacy_reserved_metadata_cannot_drop_or_reorder_facts(store):
    store.path.parent.mkdir(parents=True)
    records = [memory._coerce_record({'text': 'first', 'id': 'a'}),
               memory._coerce_record({'text': 'second', 'id': 'b'})]
    records[0].update({'_position': 'custom', 'file_path': '../../other'})
    store.path.write_text(json.dumps(records))
    assert store.records() == records
    assert store.records() == records


def test_crlf_body_roundtrips_without_normalization(store):
    text = 'first\r\nsecond\rthird'
    store.add(text)
    assert store.list() == [text]


def test_legacy_json_keeps_unknown_metadata(store):
    store.path.parent.mkdir(parents=True)
    record = memory._coerce_record({'text': 'legacy custom'})
    record['custom'] = {'arbitrary': ['猫', 123]}
    store.path.write_text(json.dumps([record]))
    assert store.records()[0] == record
    assert json.loads(store.path.read_text()) == [record]


def test_deleted_pair_does_not_restore_stale_index(store):
    store.add('old')
    for path in store.canonical_paths.values():
        path.unlink()
    assert store.add_receipt('new')['state'] == 'failed'
    assert not any(p.exists() for p in store.canonical_paths.values())


def test_unicode_marker_roundtrip_classification_and_isolation(store):
    text = '猫 café\n<!-- /record -->\n> <!-- record {} -->\n\n\\end'
    assert store.add_receipt(text)['state'] == 'created'
    store.add('I prefer coffee but kind is fact')
    store.apply([{'action': 'add', 'text': 'explicit preference', 'kind': 'preference'}])
    assert store.list()[0] == text
    assert 'explicit preference' in store.canonical_paths['USER.md'].read_text()
    assert 'I prefer coffee' in store.canonical_paths['MEMORY.md'].read_text()
    other = memory.MemoryStore('../test:other')
    assert other.list() == []
    assert other.canonical_paths != store.canonical_paths


def test_record_id_falls_back_when_host_crypto_provider_is_unavailable(store, monkeypatch):
    def unavailable_uuid():
        raise OSError('provider unavailable')

    monkeypatch.setattr(memory.uuid, 'uuid4', unavailable_uuid)
    receipt = store.add_receipt('still persists without random provider')
    assert receipt['state'] == 'created'
    assert receipt['record_id']
