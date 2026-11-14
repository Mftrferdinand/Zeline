"""Canonical reads must not acquire the derived SQLite writer."""
import sqlite3
from unittest import mock
from zeline import memory


def test_unchanged_markdown_read_does_not_reserve_writer(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path/'memory')
    monkeypatch.setattr(memory, 'LEGACY_FILE', tmp_path/'legacy.json')
    store=memory.MemoryStore('fixture:read-only')
    assert store.add_receipt('Canonical notes remain readable.')['state']=='created'
    with sqlite3.connect(store.db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        with mock.patch.object(store, '_transaction', side_effect=AssertionError('read attempted SQLite writer')):
            assert store.list()==['Canonical notes remain readable.']


def test_oversized_notes_fail_closed_without_replacing_them(tmp_path, monkeypatch):
    from zeline import memory_markdown as md
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path/'memory')
    store=memory.MemoryStore('fixture:bounded-read')
    store.add('Bounded parsing preserves the original.')
    path=store.canonical_paths['MEMORY.md']
    original=path.read_bytes()
    monkeypatch.setattr(md, 'MAX_NOTE_BYTES', 64, raising=False)
    import pytest
    with pytest.raises(ValueError, match='limit'):
        store.records()
    assert path.read_bytes()==original


def test_empty_other_owner_does_not_create_notes_or_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path/'memory')
    first=memory.MemoryStore('fixture:existing-owner')
    first.add('Existing owner canonical fact.')
    before=set(first.db_path.parent.glob('*.bak'))
    other=memory.MemoryStore('fixture:new-owner')
    assert other.list()==[]
    assert not other.canonical_paths['MEMORY.md'].exists()
    assert set(first.db_path.parent.glob('*.bak'))==before


def test_canonical_notes_survive_unavailable_derived_index(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path/'memory')
    monkeypatch.setattr(memory, 'LEGACY_FILE', tmp_path/'legacy.json')
    store=memory.MemoryStore('fixture:cache-repair')
    store.add('Canonical fact, not a cache export.')
    store.db_path.write_bytes(b'broken derived index')
    assert store.list()==['Canonical fact, not a cache export.']
