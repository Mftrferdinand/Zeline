"""Regressions for review blockers in resumable reads and stored windows."""
import pytest

from zeline import file_io, session_store


def test_invalid_utf8_page_reports_error_without_cursor(tmp_path):
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"\xff" * 60_000)

    result = file_io.read_text_window(path, limit=1)

    assert result.startswith("ERROR read file:"), result[:300]
    assert "utf-8" in result.lower()
    assert "next_byte_offset=" not in result
    assert "more_results=" not in result
    assert len(result.encode()) <= file_io.MAX_OUTPUT_BYTES


@pytest.mark.parametrize("checkpoint", [False, True])
def test_latest_tool_batch_does_not_exempt_assistant_suffix(tmp_path, checkpoint):
    persistence = session_store.SessionPersistence(tmp_path / "sessions.db")
    goal = {"role": "user", "content": "ORIGINAL_TASK: repair verified cause"}
    call = {"role": "assistant", "content": "Read evidence", "tool_calls": [
        {"id": "read-1", "type": "function", "function": {
            "name": "read_file", "arguments": '{"path":"evidence.txt"}'}}]}
    result = {"role": "tool", "tool_call_id": "read-1", "content": "VERIFIED_RESULT"}
    findings = [{"role": "assistant", "content": f"Finding {i}"} for i in range(100)]
    history = [goal, call, result, *findings]
    save = persistence.save_checkpoint if checkpoint else persistence.save

    save("review", history)
    restored, _ = session_store.SessionPersistence(persistence.path).load("review")

    stored = [m for m in restored if not m.get("content", "").startswith("[Zeline recovery]")]
    assert len(stored) <= session_store.MAX_STORED_MESSAGES
    assert goal in stored
    assert call in stored
    assert result in stored
    assert stored[-1] == findings[-1]
    assert sum(bool(m.get("tool_calls")) for m in stored) == 1
    assert sum(m.get("role") == "tool" for m in stored) == 1
    assert any(session_store.compaction.is_digest(m) for m in stored)
    assert history == [goal, call, result, *findings]
    assert any(m.get("content", "").startswith("[Zeline recovery]") for m in restored) == checkpoint
