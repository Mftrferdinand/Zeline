"""Tool evidence tests use disposable databases, never a live provider."""
import importlib
import json
import sqlite3


def test_success_evidence_is_durable_scoped_bounded_and_secret_free(tmp_path):
    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')
    secret = 'arbitrary-unlabelled-secret-value'
    event = store.record_success('owner', 'shell', {'command': secret, 'token': secret})
    assert event
    reopened = lessons.LessonsStore(store.path)
    evidence = reopened.recent_successes('owner')
    assert evidence[0]['evidence_id'] == event
    assert evidence[0]['tool'] == 'shell'
    assert evidence[0]['args_sig']
    assert reopened.recent_successes('other') == []
    for _ in range(12):
        store.record_success('owner', 'shell', {'command': secret})
    assert len(store.recent_successes('owner', limit=10000)) <= 10
    assert store.recent_successes('owner', limit=0) == []
    with sqlite3.connect(store.path) as conn:
        dump = '\n'.join(conn.iterdump())
    assert secret not in dump
    assert 'owner' not in dump
    assert secret.encode() not in store.path.read_bytes()
    assert not store.record_success('owner', 'shell', {'opaque': object()})
    assert json.dumps(evidence)


def test_explicit_resolution_requires_success_after_failure_for_same_identity_tool(tmp_path):
    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')
    old = store.record_success('owner', 'edit_file', {'path': 'old'})
    assert store.record_failure('owner', 'edit_file', {'path': 'broken'}, 'ERROR: fixture')
    assert not store.record_fix('owner', 'edit_file', 'broken', 'Try another path')
    assert store.counts('owner') == {'unresolved': 1}
    wrong_owner = store.record_success('other', 'edit_file', {'path': 'fixed'})
    wrong_tool = store.record_success('owner', 'read_file', {'path': 'fixed'})
    for evidence in ('missing', old, wrong_owner, wrong_tool):
        assert not store.record_fix('owner', 'edit_file', 'broken', 'Try another path',
                                    evidence_id=evidence)
    assert store.counts('owner') == {'unresolved': 1}
    event = store.record_success('owner', 'edit_file', {'path': 'new-strategy'})
    assert store.record_fix('owner', 'edit_file', 'broken', 'Try another path', evidence_id=event)
    assert store.counts('owner') == {'resolved': 1}
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT evidence_id FROM lessons').fetchone() == (event,)


def test_public_resolution_returns_durable_receipt_or_informative_error(tmp_path, monkeypatch):
    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')
    monkeypatch.setattr(lessons, '_store', store)
    assert store.record_failure('owner', 'edit_file', {'path': 'broken'}, 'ERROR: fixture')
    message = lessons.resolve_lesson('owner', 'edit_file', 'broken', 'Use another strategy')
    assert message.startswith('ERROR:')
    assert 'evidence' in message.lower()
    event = store.record_success('owner', 'edit_file', {'path': 'fixed'})
    receipt = json.loads(lessons.resolve_lesson('owner', 'edit_file', 'broken',
                                             'Use another strategy', evidence_id=event))
    assert receipt == dict(version=1, kind='lesson', state='updated', record_id='1',
                           revision=1, summary='Use another strategy', evidence_id=event)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT id, revision, evidence_id FROM lessons').fetchone() == (
            int(receipt['record_id']), receipt['revision'], receipt['evidence_id'])


def test_classified_success_capture_and_exact_retry_link_only_matching_call(tmp_path, monkeypatch):
    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')
    monkeypatch.setattr(lessons, '_store', store)
    args = {'path': 'same', 'content': 'old'}
    store.record_failure('owner', 'write_file', args, 'ERROR: fixture')
    lessons.log_success('owner', 'write_file', args, 'ERROR: failed')
    assert store.recent_successes('owner') == []
    lessons.log_success('owner', 'write_file', {'path': 'same', 'content': 'other'}, 'OK')
    assert len(store.recent_successes('owner')) == 1
    assert store.counts('owner') == {'unresolved': 1}
    lessons.log_success('owner', 'write_file', args, 'OK')
    assert store.counts('owner') == {'resolved': 1}
    with sqlite3.connect(store.path) as conn:
        evidence_id, call_hash = conn.execute('SELECT evidence_id, call_hash FROM lessons').fetchone()
        assert conn.execute('SELECT call_hash FROM lesson_successes WHERE id = ?',
                            (evidence_id,)).fetchone() == (call_hash,)


def test_legacy_rows_are_preserved_without_fabricating_evidence(tmp_path):
    lessons = importlib.import_module('zeline.lessons')
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY, key TEXT NOT NULL, "
                     "tool TEXT NOT NULL, args_sig TEXT NOT NULL, error TEXT, fix TEXT, "
                     "status TEXT NOT NULL, ts REAL NOT NULL, resolved_ts REAL)")
        conn.execute("INSERT INTO lessons VALUES (1, ?, 'read_file', 'read_file(path=x)', "
                     "'ERROR: old', 'historical fix', 'resolved', 1, 2)",
                     (lessons._key('owner'),))
    lessons.LessonsStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT fix, status, evidence_id, revision FROM lessons').fetchone() == (
            'historical fix', 'resolved', None, 0)
        assert conn.execute('SELECT COUNT(*) FROM lesson_successes').fetchone() == (0,)


def test_auto_resolution_rejects_unrelated_or_older_linked_evidence(tmp_path):
    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')
    args = {'path': 'x', 'content': 'first'}
    old = store.record_success('owner', 'write_file', args)
    store.record_failure('owner', 'write_file', args, 'ERROR: fixture')
    other = store.record_success('owner', 'write_file', {'path': 'x', 'content': 'second'})
    outsider = store.record_success('outsider', 'write_file', args)
    for evidence in (old, other, outsider, 'missing'):
        assert not store.record_fix_auto('owner', 'write_file', args, evidence_id=evidence)
    assert store.counts('owner') == {'unresolved': 1}


def test_success_evidence_storage_failure_is_best_effort(tmp_path, monkeypatch):
    lessons = importlib.import_module('zeline.lessons')
    store = lessons.LessonsStore(tmp_path / 'lessons.db')

    def unavailable():
        raise sqlite3.OperationalError('fixture locked')

    monkeypatch.setattr(store, '_connect', unavailable)
    assert store.record_success('owner', 'shell', {'command': 'true'}) == ''
    assert store.recent_successes('owner') == []
    assert not store.record_fix('owner', 'shell', 'shell', 'retry', evidence_id='missing')
