"""Fault injection across the canonical pair and derived index commit."""
import json
import sqlite3
from contextlib import contextmanager
from zeline import memory
from zeline import memory_markdown as md


def test_cache_commit_failure_rolls_back_canonical_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path/'memory')
    store=memory.MemoryStore('fixture:commit-fault')
    store.add('Before failed batch.')
    before={name:path.read_bytes() for name,path in store.canonical_paths.items()}
    original=store._indexed_transaction
    @contextmanager
    def failing(*args, **kwargs):
        with original(*args, **kwargs) as value:
            yield value
            raise sqlite3.OperationalError('injected index commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(store, '_indexed_transaction', failing)
        result=store.apply([{'action':'add','text':'Must not partially commit.'}])
    assert result['success'] is False
    assert {name:path.read_bytes() for name,path in store.canonical_paths.items()}==before
    assert store.list()==['Before failed batch.']


def test_interrupted_pair_is_recovered_before_returning_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path/'memory')
    store=memory.MemoryStore('fixture:crash-recovery')
    store.add('Committed canonical note.')
    paths=store.canonical_paths
    before={name:path.read_text() for name,path in paths.items()}
    journal=paths['MEMORY.md'].parent/'.transaction.json'
    journal.write_text(json.dumps(before))
    paths['MEMORY.md'].write_text('partial uncommitted replacement')
    assert memory.MemoryStore(store.identity).list()==['Committed canonical note.']
    assert not journal.exists()
    assert {name:path.read_text() for name,path in paths.items()}==before
