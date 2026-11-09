"""Disposable source-runtime probes; no live providers or user state.

Run only through test_reliability_corpus.py, which isolates config before import.
All scenarios are synthetic fixtures, including real process crashes.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def deny_network(*args, **kwargs):
    raise AssertionError("network forbidden in reliability fixture")


def append_effect(path):
    with path.open("a") as handle:
        handle.write("fixture-effect\n")
        handle.flush()
        os.fsync(handle.fileno())


def crash_worker(mode, workspace):
    from zeline.durable_jobs import JobStore, Worker
    store = JobStore(workspace / "jobs.sqlite3", clock=lambda: 100.0)
    def spawn(job, should_stop):
        if mode != "before-effect":
            append_effect(workspace / "effects.txt")
        if mode == "after-commit":
            assert store.complete(job["id"], job["lease_token"], result="fixture committed")
        os._exit(73)  # Deliberately bypass Python finally/worker result writes.
    Worker(store=store, spawn=spawn, lease_seconds=60).run_once()
    raise AssertionError("fixture job was not claimed")


def turn_crash_worker(mode, workspace):
    from zeline.agent import Zeline
    from zeline.session_store import SessionPersistence
    store = SessionPersistence(workspace / "session.sqlite3")
    agent = Zeline(identity="fixture:restart", tool_profile="workspace", workspace=str(workspace))
    tool_call = {"id": "fixture_write", "type": "function", "function": {
        "name": "write_file", "arguments": json.dumps({"path": "artifact.txt", "content": "fixture artifact"})}}
    agent._call_llm = lambda **kwargs: {"content": "", "tool_calls": [tool_call]}
    original = agent.executor.run
    def run(name, args):
        result = original(name, args)
        append_effect(workspace / "effects.txt")
        if mode == "after-effect":
            os._exit(73)
        return result
    agent.executor.run = run
    def checkpoint(messages):
        store.save_checkpoint(agent.identity, messages)
        has_call = any(m.get("tool_calls") for m in messages)
        has_result = any(m.get("role") == "tool" for m in messages)
        if (mode == "before-effect" and has_call) or (mode == "after-checkpoint" and has_result):
            os._exit(73)
    agent.send("Write fixture artifact once.", on_checkpoint=checkpoint)
    raise AssertionError("crash boundary was not exercised")


def archive_isolation(workspace, long=False):
    from zeline import compaction
    a, b = (("prefix" * 20 + ":A", "prefix" * 20 + ":B") if long else ("fixture:a:b", "fixture:a_b"))
    first = compaction.archive([{"role": "user", "content": "FIXTURE_A_ONLY"}], a)
    second = compaction.archive([{"role": "user", "content": "FIXTURE_B_ONLY"}], b)
    assert first is not None and second is not None
    assert first != second, "distinct identities share the same transcript archive"
    assert "FIXTURE_B_ONLY" not in first.read_text()
    assert "FIXTURE_A_ONLY" not in second.read_text()
    again = compaction.archive([{"role": "assistant", "content": "A_AGAIN"}], a)
    assert again == first and "A_AGAIN" in first.read_text()
    return {"archive_paths_distinct": True, "cross_marker_absent": True}


def legacy_archive_preserved(workspace):
    from zeline import compaction
    identity = "fixture:legacy"
    root = compaction.archive_root()
    root.mkdir(parents=True, exist_ok=True)
    import time
    legacy = root / ("fixture_legacy-" + time.strftime("%Y-%m-%d", time.gmtime()) + ".md")
    legacy.write_text("AMBIGUOUS_LEGACY_FIXTURE")
    before = legacy.read_bytes()
    current = compaction.archive([{"role": "user", "content": "NEW_OWNER_FIXTURE"}], identity)
    assert current is not None and current != legacy
    assert legacy.read_bytes() == before
    assert "AMBIGUOUS_LEGACY_FIXTURE" not in current.read_text()
    return {"legacy_unchanged": True, "automatic_merge": False}


def archive_reader_scope(workspace, mode):
    from zeline import compaction
    from zeline.tools import ToolExecutor
    a, b = "fixture:read-owner-a", "fixture:read-owner-b"
    target = compaction.archive([{"role": "user", "content": "FIXTURE_OWNER_A_ARCHIVE"}], a)
    assert target is not None
    own = mode in {"own", "search-own"}
    trusted = mode in {"full", "search-full"}
    search_mode = mode in {"search-foreign", "search-own", "search-full", "search-legacy", "search-ambiguous"}
    if mode == "search-ambiguous":
        # Generated legacy filename: policy must see the whole canonical path,
        # never a prefix inferred from ':line:' or a newline-delimited row.
        import os
        if os.name == "nt":
            return {"status": "skipped", "reason": "requires POSIX filenames"}
        target = compaction.archive_root() / "legacy:12:\nambiguous.md"
        target.write_text("FIXTURE_OWNER_A_ARCHIVE")
    if mode in {"legacy", "search-legacy"}:
        target = compaction.archive_root() / "legacy_ambiguous.md"
        target.write_text("FIXTURE_OWNER_A_ARCHIVE")
    root = compaction.archive_root().parent if mode == "shared-root" or search_mode else workspace
    reader = ToolExecutor(a if own or trusted else b, profile="full" if trusted else "workspace", workspace=str(root))
    if search_mode:
        result = reader.run("search_files", {"query": "FIXTURE_OWNER_A_ARCHIVE", "limit": 30})
        files_result = reader.run("search_files", {"query": "*", "target": "files", "limit": 100})
        archive_name = target.name
        if own or trusted:
            assert "FIXTURE_OWNER_A_ARCHIVE" in result, result
            assert archive_name in files_result, files_result
            return {"access": "allowed", "mode": mode}
        assert "FIXTURE_OWNER_A_ARCHIVE" not in result, "foreign archive content indexed by search_files"
        assert archive_name not in files_result, "foreign archive filename indexed by search_files"
        return {"access": "denied", "mode": mode}
    requested = str(target)
    if mode == "symlink":
        link = workspace / "borrowed.md"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            return {"status": "skipped", "reason": "fixture symlink unavailable"}
        requested = str(link)
    result = reader.run("read_file", {"path": requested, "limit": 30})
    if mode in {"own", "full"}:
        assert "FIXTURE_OWNER_A_ARCHIVE" in result, result
        return {"access": "allowed", "mode": mode}
    assert result.startswith("ERROR") and "FIXTURE_OWNER_A_ARCHIVE" not in result, "foreign/ambiguous archive content exposed"
    return {"access": "denied", "mode": mode}


def store_isolation(workspace):
    from zeline.memory import MemoryStore
    from zeline.session_store import SessionPersistence
    from zeline.durable_jobs import JobStore
    a, b = "fixture:a:b", "fixture:a_b"
    ma, mb = MemoryStore(a), MemoryStore(b)
    ma.add("FIXTURE_A_ONLY")
    mb.add("FIXTURE_B_ONLY")
    assert "FIXTURE_A_ONLY" in ma.list() and "FIXTURE_B_ONLY" not in ma.list()
    assert "FIXTURE_B_ONLY" in mb.list() and "FIXTURE_A_ONLY" not in mb.list()
    sessions = SessionPersistence(workspace / "session.sqlite3")
    sessions.save(a, [{"role": "user", "content": "FIXTURE_A_ONLY"}])
    sessions.save(b, [{"role": "user", "content": "FIXTURE_B_ONLY"}])
    sessions.save_checkpoint(a, [{"role": "user", "content": "A_CHECKPOINT"}])
    assert "A_CHECKPOINT" in json.dumps(sessions.load(a))
    assert "A_CHECKPOINT" not in json.dumps(sessions.load(b))
    assert "FIXTURE_B_ONLY" not in json.dumps(sessions.load(a))
    jobs = JobStore(workspace / "jobs.sqlite3")
    job = jobs.submit(a, [{"goal": "fixture A"}], profile="safe", workspace=workspace)[0]
    assert jobs.get(b, job) is None and not jobs.list(b) and not jobs.results(b)
    assert not jobs.cancel(b, job) and not jobs.ack(b, job)
    try:
        jobs.submit(b, [{"goal": "fixture dependency", "depends_on": [job]}], profile="safe", workspace=workspace)
    except ValueError:
        pass
    else:
        raise AssertionError("foreign dependency accepted")
    assert not jobs.list(b)
    return {"memory": "isolated", "session_checkpoint": "isolated", "job_views_mutations_dependencies": "isolated"}


def compact_twice(workspace):
    from zeline.agent import Zeline
    from zeline import compaction
    agent = Zeline(identity="fixture:compact", tool_profile="workspace", workspace=str(workspace),
                   system_extra="FIXTURE_SYSTEM_CONSTRAINT: do not overwrite verified artifacts")
    system = agent.messages[0]
    agent.messages.extend([
        {"role": "user", "content": "Create fixture artifact.txt and retain evidence."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "fixture_done", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"artifact.txt","content":"fixture"}'}}]},
        {"role": "tool", "tool_call_id": "fixture_done", "content": "FIXTURE_VERIFIED_WRITE"},
        {"role": "assistant", "content": "Artifact verified; next inspect results."}])
    for n in range(35):
        agent.messages.extend([{"role": "user", "content": f"fixture follow-up {n}"}, {"role": "assistant", "content": f"fixture response {n}"}])
    def no_tool(*args, **kwargs):
        raise AssertionError("compaction must not execute tools")
    agent.executor.run = no_tool
    for round_number in range(2):
        agent._trim_history()
        assert agent.messages[0] is system
        digests = [m for m in agent.messages if compaction.is_digest(m)]
        assert len(digests) == 1
        assert "FIXTURE_VERIFIED_WRITE" in digests[0]["content"]
        assert "artifact.txt" in digests[0]["content"]
        assert "UNKNOWN" in digests[0]["content"]
        assert len(digests[0]["content"]) <= compaction.MAX_DIGEST_CHARS + compaction.MAX_TASK_BLOCK_CHARS + 20
        if round_number == 0:
            for n in range(40):
                agent.messages.extend([{"role": "user", "content": f"fixture second round {n}"}, {"role": "assistant", "content": "fixture response"}])
    assert "FIXTURE_SYSTEM_CONSTRAINT" in system["content"]
    return {"compactions": 2, "system_preserved": True, "completed_evidence_retained": True, "tool_replay": False}


def crash_case(workspace, mode, turn=False):
    from zeline.durable_jobs import JobStore
    if not turn:
        initial = JobStore(workspace / "jobs.sqlite3", clock=lambda: 100.0)
        job_id = initial.submit("fixture:owner", [{"goal": "fixture effect", "retry_safe": False}], profile="workspace", workspace=workspace)[0]
    command = [sys.executable, str(Path(__file__).resolve()), "--turn-worker" if turn else "--job-worker", mode, str(workspace)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert process.returncode == 73, (process.returncode, process.stdout, process.stderr)
    effects = workspace / "effects.txt"
    before = effects.read_text().splitlines() if effects.exists() else []
    assert len(before) == (0 if mode == "before-effect" else 1)
    if turn:
        from zeline.agent import Zeline
        from zeline.session_store import SessionPersistence
        store = SessionPersistence(workspace / "session.sqlite3")
        messages, _ = store.load("fixture:restart")
        assert "UNKNOWN" in json.dumps(messages)
        evidence = [m for m in messages if m.get("role") == "tool"]
        assert bool(evidence) == (mode == "after-checkpoint")
        agent = Zeline(identity="fixture:restart", tool_profile="workspace", workspace=str(workspace))
        agent.messages = [agent.messages[0], *[m for m in messages if m.get("role") != "system"]]
        agent.executor.run = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("automatic tool replay"))
        agent._call_llm = lambda **kwargs: {"content": "fixture resume: verify state before further mutations"}
        assert agent.send("Continue without repeating mutations.").startswith("fixture resume")
        state = "checkpoint_evidence" if evidence else "outcome_unknown"
    else:
        recovered = JobStore(workspace / "jobs.sqlite3", clock=lambda: 1000.0)
        assert recovered.claim("restart-worker") is None
        row = recovered.get("fixture:owner", job_id)
        assert row["attempts"] == 1
        if mode == "after-commit":
            assert row["state"] == "succeeded" and row["result"] == "fixture committed"
        else:
            assert row["state"] == "failed" and "outcome uncertain" in row["error"]
        state = row["state"]
        assert recovered.claim("another-worker") is None
    after = effects.read_text().splitlines() if effects.exists() else []
    assert before == after
    return {"actual_child_exit": process.returncode, "effect_count": len(after), "automatic_replay": False,
            "recovery": state, "exactly_once_guarantee": False}


def main():
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    action, case, path = sys.argv[1:]
    workspace = Path(path)
    if action == "--job-worker":
        crash_worker(case, workspace)
    elif action == "--turn-worker":
        turn_crash_worker(case, workspace)
    else:
        if case == "archive-punctuation":
            result = archive_isolation(workspace)
        elif case == "archive-long":
            result = archive_isolation(workspace, long=True)
        elif case == "archive-legacy":
            result = legacy_archive_preserved(workspace)
        elif case.startswith("reader-"):
            result = archive_reader_scope(workspace, case[7:])
        elif case == "store-isolation":
            result = store_isolation(workspace)
        elif case == "compact-twice":
            result = compact_twice(workspace)
        elif case.startswith("job-"):
            result = crash_case(workspace, case[4:])
        elif case.startswith("turn-"):
            result = crash_case(workspace, case[5:], turn=True)
        else:
            raise ValueError("unknown fixture case")
        try:
            source_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            source_sha = None
        provenance = {"schema_version": "1.0", "data_kind": "synthetic", "source_sha": source_sha,
                      "runtime_sha": None, "model": None,
                      "provider": "controlled-fixture" if case.startswith("turn-") else None, "workload_id": case}
        hashes = {name: hashlib.sha256((ROOT / "zeline" / name).read_bytes()).hexdigest()
                  for name in ("compaction.py", "agent.py", "session_store.py", "durable_jobs.py", "memory.py", "tools.py")}
        print(json.dumps({"case": case, "status": result.get("status", "passed"), "data_kind": "synthetic", "kind": "component_contract",
                          "provenance": provenance, "verification_source_files_sha256": hashes,
                          "evidence": result}))


if __name__ == "__main__":
    main()
