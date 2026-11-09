"""Regression contracts for findings from the independent learning-loop review."""
import importlib


def test_new_memory_is_not_retired_during_its_own_admission(tmp_path, monkeypatch):
    memory = importlib.import_module('zeline.memory')
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    monkeypatch.setattr(memory, 'LEGACY_FILE', tmp_path / 'legacy.json')
    monkeypatch.setattr(memory, 'CONSOLIDATE_AT_CHARACTERS', 50)
    monkeypatch.setattr(memory, 'MAX_CHARACTERS_PER_IDENTITY', 80)
    store = memory.MemoryStore('review:owner')
    assert store.add_receipt('operator-A-' + 'a' * 20)['state'] == 'created'
    assert store.add_receipt('operator-B-' + 'b' * 20)['state'] == 'created'
    original = store.records()
    receipt = store.add_receipt('cobalt correction', source='reflection', origin_turn='review-admission')
    assert receipt['state'] == 'created'
    records = store.records()
    saved = next((row for row in records if row['id'] == receipt['record_id']), None)
    assert saved is not None, 'created receipt must identify a committed live record'
    assert saved['revision'] == receipt['revision']
    assert [row for row in records if row['source'] == 'user'] == original
    assert any(row['id'] == saved['id'] for row in store.recall('cobalt')['records'])


def test_auto_resolution_cannot_overwrite_a_committed_explicit_fix(tmp_path, monkeypatch):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys

    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')
    identity, tool, args = 'review:owner', 'shell', {'command': 'fixture-call'}
    assert store.record_failure(identity, tool, args, 'temporary failure')
    evidence = store.record_success(identity, tool, args)
    lesson_id = store.unresolved(identity)[0]['id']
    native_connect = store._connect
    explicit_receipts = []

    class Selection:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            observed = self.cursor.fetchone()
            if observed is not None and not explicit_receipts:
                # A separate interpreter has its own module-level lock. Commit
                # the operator's correction after auto selected stale state.
                env = os.environ.copy()
                env['PYTHONPATH'] = str(Path(lessons.__file__).resolve().parent.parent)
                code = (
                    'import json,sys; from pathlib import Path; from zeline.lessons import LessonsStore; '
                    's=LessonsStore(Path(sys.argv[1])); '
                    'r=s.record_fix("review:owner","shell","lesson:"+sys.argv[2],'
                    '"explicit correction retained",sys.argv[3]); '
                    'print(json.dumps(r)); sys.exit(0 if r else 1)'
                )
                result = subprocess.run([sys.executable, '-c', code, str(store.path), str(lesson_id), evidence],
                                        env=env, capture_output=True, text=True, timeout=10)
                assert result.returncode == 0, (result.stdout, result.stderr)
                explicit_receipts.append(json.loads(result.stdout))
            return observed

    class Connection:
        def __init__(self):
            self.connection = native_connect()

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            return Selection(cursor) if sql.startswith('SELECT id, error, ts') else cursor

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *exc):
            return self.connection.__exit__(*exc)

        def close(self):
            self.connection.close()

    monkeypatch.setattr(store, '_connect', Connection)
    automatic = store.record_fix_auto(identity, tool, args, evidence)
    assert explicit_receipts and explicit_receipts[0]['state'] == 'updated'
    assert automatic is False, 'stale automatic resolver must not overwrite the committed explicit fix'
    resolved = store.resolved(identity)[0]
    assert resolved['fix'] == 'explicit correction retained'
    assert resolved['evidence_id'] == evidence
    assert resolved['revision'] == explicit_receipts[0]['revision']


def test_batch_admission_keeps_its_new_reflection_memory_live(tmp_path, monkeypatch):
    memory = importlib.import_module('zeline.memory')
    monkeypatch.setattr(memory, 'MEMORY_DIR', tmp_path / 'memory')
    monkeypatch.setattr(memory, 'LEGACY_FILE', tmp_path / 'legacy.json')
    monkeypatch.setattr(memory, 'CONSOLIDATE_AT_CHARACTERS', 50)
    monkeypatch.setattr(memory, 'MAX_CHARACTERS_PER_IDENTITY', 80)
    store = memory.MemoryStore('review:batch')
    store.add('operator-A-' + 'a' * 20)
    store.add('operator-B-' + 'b' * 20)
    store.default_source = 'reflection'
    result = store.apply([{'action': 'add', 'text': 'cobalt correction'}])
    assert result['success'] is True
    assert set(result['changed']) <= {row['id'] for row in store.records()}
