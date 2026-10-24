"""Adversarial provenance, bounded storage and fresh-process replay."""
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_success_retention_preserves_linked_evidence(tmp_path, monkeypatch):
    module = importlib.import_module('zeline.lessons')
    monkeypatch.setattr(module, 'MAX_SUCCESS_EVENTS_PER_IDENTITY', 2, raising=False)
    store = module.LessonsStore(tmp_path / 'lessons.db')
    store.record_failure('owner', 'read_file', {'path': 'missing'}, 'ERROR')
    linked = store.record_success('owner', 'read_file', {'path': 'fixed'})
    assert store.record_fix('owner', 'read_file', 'missing', 'Use corrected path', evidence_id=linked)
    for i in range(6):
        store.record_success('owner', 'read_file', {'path': str(i)})
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM lesson_successes').fetchone()[0] <= 3
        assert db.execute('SELECT id FROM lesson_successes WHERE id=?', (linked,)).fetchone()


def test_ambiguous_lesson_selector_cannot_resolve_arbitrary_failure(tmp_path):
    module = importlib.import_module('zeline.lessons')
    store = module.LessonsStore(tmp_path / 'lessons.db')
    store.record_failure('owner', 'web_search', {'query': 'one'}, 'ERROR')
    store.record_failure('owner', 'web_search', {'query': 'two'}, 'ERROR')
    event = store.record_success('owner', 'web_search', {'query': 'working'})
    assert not store.record_fix('owner', 'web_search', 'web_search', 'Use working query', evidence_id=event)
    lesson = store.unresolved('owner')[0]
    assert store.record_fix('owner', 'web_search', 'lesson:' + str(lesson['id']), 'Use working query', evidence_id=event)


def test_lesson_prompt_never_promotes_operational_success_to_task_quality(tmp_path):
    module = importlib.import_module('zeline.lessons')
    store = module.LessonsStore(tmp_path / 'lessons.db')
    store.record_failure('owner', 'read_file', {'path': 'missing'}, 'ERROR')
    event = store.record_success('owner', 'read_file', {'path': 'working'})
    store.record_fix('owner', 'read_file', 'missing', 'Use corrected path', evidence_id=event)
    prompt = store.prompt_block('owner')
    assert event in prompt and 'task benefit unmeasured' in prompt
    assert 'fixes that worked' not in prompt
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE lessons SET evidence_id=NULL,revision=0')
    assert 'legacy/unlinked' in store.prompt_block('owner')


def test_memory_recall_survives_fresh_python_process(tmp_path, monkeypatch):
    from zeline import memory
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    store = memory.MemoryStore('process-owner')
    store.default_source = 'reflection'
    added = store.add_receipt('For the source fixture inspect source before patching.')
    script = """from pathlib import Path
import json,sys
from zeline import memory
memory.MEMORY_DIR=Path(sys.argv[1])
store=memory.MemoryStore('process-owner')
records=store.recall('source fixture patching')['records']
store.record_retrieval('new-process',records)
print(json.dumps([{'id':r['id'],'revision':r['revision']} for r in records]))
"""
    env = {**os.environ, 'PYTHONPATH': str(Path.cwd()), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-c', script, str(store.db_path.parent)],
                            capture_output=True, text=True, env=env, timeout=20, check=True)
    assert json.loads(result.stdout) == [{'id': added['record_id'], 'revision': 1}]
    assert store.retrievals('new-process')[0]['stage'] == 'retrieved'
