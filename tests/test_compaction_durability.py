"""Offline regression tests for lossless, fail-closed history compaction."""

import json
import os
import stat
from types import SimpleNamespace

import pytest

from zeline import compaction


def envelopes(text):
    return [json.loads(block.split("\n```", 1)[0])
            for block in text.split("```json message-envelope\n")[1:]]


def test_archive_preserves_exact_envelopes_and_long_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(compaction.config, "DATA_DIR", tmp_path)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "request"}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "exact-id", "type": "function", "extra": "metadata", "function": {
                "name": "write_file", "arguments": '{  "content": "' + "Z" * 2000 + '" }'}},
            {"id": "broken-id", "function": {"name": "other", "arguments": "{not JSON"}},
        ], "reasoning_content": "keep envelope"},
        {"role": "tool", "tool_call_id": "exact-id", "name": "write_file", "content": "ok"},
    ]
    path = compaction.archive(messages, "durability")
    assert path is not None
    assert envelopes(path.read_text()) == messages
    assert "Z" * 2000 in compaction.render(messages)
    assert "{not JSON" in compaction.render(messages)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_archive_success_is_not_pruned_before_trim(tmp_path, monkeypatch):
    monkeypatch.setattr(compaction.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(compaction, "MAX_TOTAL_BYTES", 1)
    path = compaction.archive([{"role": "user", "content": "keep"}], "large")
    assert path is not None and path.exists()


def test_archive_fsync_failure_cannot_authorize_compaction(tmp_path, monkeypatch):
    monkeypatch.setattr(compaction.config, "DATA_DIR", tmp_path)
    def fail(_fd):
        raise OSError("disk full")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        compaction.compact([{"role": "user", "content": "must survive"}], "failure")


@pytest.fixture
def trim_agent(tmp_path, monkeypatch):
    # Import under isolated state: no provider calls or owner task-board reads.
    monkeypatch.setenv("ZELINE_HOME", str(tmp_path))
    from zeline.agent import Zeline
    import zeline.agent as module
    monkeypatch.setattr(module.compaction.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module.compaction, "_open_task_block", lambda _: "")
    return SimpleNamespace(messages=[], identity="offline"), Zeline._trim_history, module.compaction


@pytest.mark.parametrize("failure", ["none", "raise"])
def test_trim_archive_failure_retains_original_objects(trim_agent, monkeypatch, failure):
    agent, trim, module = trim_agent
    original = [{"role": "system", "content": "system"},
                {"role": "user", "content": "old" * 15000},
                {"role": "user", "content": "current"}]
    agent.messages = original
    if failure == "none":
        monkeypatch.setattr(module, "archive", lambda *_: None)
    else:
        def fail(*_):
            raise OSError("disk full")
        monkeypatch.setattr(module, "compact", fail)
    trim(agent)
    assert agent.messages is original


@pytest.mark.parametrize("large_arguments", [False, True])
def test_oversized_single_turn_is_bounded_at_complete_parallel_tool_groups(trim_agent, large_arguments):
    agent, trim, module = trim_agent
    user = {"role": "user", "content": "CURRENT_TASK", "task_id": "isolation"}
    groups = []
    for index in range(10 if large_arguments else 35):
        ids = [f"{index}-a", f"{index}-b"]
        groups.extend([
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": id_, "function": {"name": "read_file", "arguments":
                    json.dumps({"path": "p", "extra": "a" * (1800 if large_arguments else 0)})}}
                for id_ in ids]},
            *[{"role": "tool", "tool_call_id": id_, "content": "r" * (1 if large_arguments else 1000)} for id_ in ids],
        ])
    agent.messages = [{"role": "system", "content": "system"}, user, *groups]
    trim(agent)
    assert next(m for m in reversed(agent.messages) if m["role"] == "user") is user
    assert len(agent.messages[1:]) <= 60
    assert sum(len(json.dumps(m)) for m in agent.messages[1:]) <= 30000
    pending = set()
    for message in agent.messages:
        if message.get("tool_calls"):
            assert not pending
            pending = {c["id"] for c in message["tool_calls"]}
        if message["role"] == "tool":
            pending.remove(message["tool_call_id"])
    assert not pending
    saved = envelopes(next(module.archive_root().glob("*.md")).read_text())
    retained = [m for m in agent.messages if m in groups]
    assert saved + retained == groups


def test_legacy_transcript_remains_searchable_after_append(tmp_path, monkeypatch):
    monkeypatch.setattr(compaction.config, "DATA_DIR", tmp_path)
    path = compaction.archive([{"role": "user", "content": "first"}], "legacy")
    legacy = "## Evicted earlier\n### user\nOLD_SEARCHABLE_EVIDENCE\n"
    path.write_text(legacy)
    message = {"role": "tool", "tool_call_id": "original", "content": "NEW_EVIDENCE"}
    assert compaction.archive([message], "legacy") == path
    assert path.read_text().startswith(legacy)
    assert envelopes(path.read_text()) == [message]


@pytest.mark.parametrize("kind", ["user", "incomplete"])
def test_indivisible_current_request_or_incomplete_group_is_preserved(trim_agent, kind):
    agent, trim, _ = trim_agent
    user = {"role": "user", "content": "x" * (40000 if kind == "user" else 1)}
    original = [{"role": "system", "content": "system"}, user]
    if kind == "incomplete":
        original.append({"role": "assistant", "content": "x" * 40000, "tool_calls": [
            {"id": "pending", "function": {"name": "tool", "arguments": "{}"}}]})
    agent.messages = original
    trim(agent)
    assert agent.messages is original
