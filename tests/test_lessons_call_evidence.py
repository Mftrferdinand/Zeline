"""A display signature cannot prove equality of the actual tool calls."""
import pytest


@pytest.mark.parametrize('tool,failed,succeeded', [
    ('read_file', {'path': '/tmp/' + 'a' * 90 + '/failed.txt'},
     {'path': '/tmp/' + 'a' * 90 + '/different.txt'}),
    ('web_search', {'query': 'failed query'}, {'query': 'different query'}),
    ('write_file', {'path': 'same.txt', 'content': 'first payload'},
     {'path': 'same.txt', 'content': 'different payload'}),
])
def test_different_full_call_cannot_auto_resolve_failure(tmp_path, tool, failed, succeeded):
    from zeline.lessons import LessonsStore
    store = LessonsStore(tmp_path / 'lessons.db')
    assert store.record_failure('owner', tool, failed, 'ERROR: fixture failure')
    assert not store.record_fix_auto('owner', tool, succeeded)
    assert store.counts('owner').get('unresolved') == 1
    assert store.record_fix_auto('owner', tool, failed)
    assert store.counts('owner').get('resolved') == 1


def test_distinct_calls_do_not_deduplicate_on_display_signature(tmp_path):
    from zeline.lessons import LessonsStore
    store = LessonsStore(tmp_path / 'lessons.db')
    assert store.record_failure('owner', 'web_search', {'query': 'first'}, 'ERROR: unavailable')
    assert store.record_failure('owner', 'web_search', {'query': 'second'}, 'ERROR: unavailable')
    assert store.counts('owner').get('unresolved') == 2


def test_call_fingerprint_is_order_independent_and_does_not_store_raw_payload(tmp_path):
    import sqlite3
    from zeline.lessons import LessonsStore
    path = tmp_path / 'lessons.db'
    store = LessonsStore(path)
    secret = 'FIXTURE_PRIVATE_PAYLOAD_NEVER_PERSIST'
    args = {'path': 'same.txt', 'payload': secret}
    assert store.record_failure('owner', 'write_file', args, 'ERROR: unavailable')
    with sqlite3.connect(path) as connection:
        value = connection.execute('SELECT call_hash FROM lessons').fetchone()[0]
    assert len(value) == 64
    assert secret.encode() not in path.read_bytes()
    assert store.record_fix_auto('owner', 'write_file', {'payload': secret, 'path': 'same.txt'})


def test_legacy_lessons_are_preserved_but_not_guessed_into_verified_retries(tmp_path):
    import sqlite3
    import time
    from zeline.lessons import LessonsStore, _args_signature, _key
    path = tmp_path / 'legacy.db'
    args = {'path': 'legacy.txt'}
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE lessons (id INTEGER PRIMARY KEY, key TEXT, tool TEXT, '
                           'args_sig TEXT, error TEXT, fix TEXT, status TEXT, ts REAL, resolved_ts REAL)')
        connection.execute('INSERT INTO lessons VALUES (7, ?, ?, ?, ?, NULL, ?, ?, NULL)',
                           (_key('owner'), 'read_file', _args_signature('read_file', args),
                            'ERROR: fixture', 'unresolved', time.time()))
    store = LessonsStore(path)
    assert not store.record_fix_auto('owner', 'read_file', args)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT id, status, call_hash FROM lessons').fetchone() == (7, 'unresolved', None)


def test_non_json_call_does_not_create_or_resolve_evidence(tmp_path):
    from zeline.lessons import LessonsStore
    store = LessonsStore(tmp_path / 'lessons.db')
    assert not store.record_failure('owner', 'read_file', {'opaque': object()}, 'ERROR: fixture')
    assert not store.record_fix_auto('owner', 'read_file', {'opaque': object()})
