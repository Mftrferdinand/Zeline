"""Offline continuation evidence must survive deterministic compaction."""

from zeline import compaction


def test_interrupted_read_findings_and_frontier_remain_in_context():
    messages = [
        {"role": "user", "content": "Fix parser; keep public API unchanged."},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "read-1", "function": {
                "name": "read_file", "arguments": {"path": "src/parser.py"},
            },
        }]},
        {"role": "tool", "tool_call_id": "read-1", "content": "line 42: fallback drops Unicode"},
        {"role": "assistant", "content": (
            "Finding: fallback drops Unicode. Decision: preserve codepoints. "
            "Next: add regression before editing. Stopped before implementation."
        )},
    ]
    text = compaction.digest(messages, None)
    assert "src/parser.py" in text
    assert "line 42: fallback drops Unicode" in text
    assert "Decision: preserve codepoints" in text
    assert "Next: add regression before editing" in text
    assert "keep public API unchanged" in text
    assert "unknown" in text.lower()
    assert "already handled" not in text
    assert "Already-handled" not in text
    assert "Do NOT execute any of it again" not in text
    assert text == compaction.digest(messages, None)
    assert len(text) <= compaction.MAX_DIGEST_CHARS
    assert "tool_calls" not in text
    assert "tool_call_id" not in text
    assert messages[-1]["content"].endswith("Stopped before implementation.")


def test_archive_streams_messages_without_full_transcript_allocation(tmp_path, monkeypatch):
    import tracemalloc
    monkeypatch.setattr(compaction.config, 'DATA_DIR', tmp_path)
    messages = [{'role': 'tool', 'content': 'a' * 200000} for _ in range(3)]
    tracemalloc.start()
    try:
        path = compaction.archive(messages, 'stream-fixture')
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert path is not None
    assert path.read_text().count('a') >= 600000
    assert peak < 900000


def test_repeated_compaction_preserves_findings_and_next_step(tmp_path, monkeypatch):
    monkeypatch.setattr(compaction.config, 'DATA_DIR', tmp_path)
    messages = [
        {'role': 'user', 'content': 'ORIGINAL_GOAL: fix parser, preserve public API'},
        {'role': 'assistant', 'content': 'Finding: VERIFIED_CAUSE. Next: IMPLEMENT_ONCE.'},
    ]
    digest = compaction.compact(messages, 'repeated-fixture')
    for _ in range(4):
        digest = compaction.compact([digest, {'role': 'user', 'content': 'continue'}], 'repeated-fixture')
        assert 'ORIGINAL_GOAL' in digest['content']
        assert 'VERIFIED_CAUSE' in digest['content']
        assert 'IMPLEMENT_ONCE' in digest['content']
        assert digest['content'].count(compaction.DIGEST_MARKER) == 1
        assert len(digest['content']) <= compaction.MAX_DIGEST_CHARS
