"""Backend integration checks in bounded, disposable subprocesses."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def _run_isolated(source: str) -> None:
    # Keep Termux PATH/LD_PRELOAD and interpreter support, but never inherit the
    # operator's Zeline paths, credentials, policies or active session state.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("ZELINE_")}
    with tempfile.TemporaryDirectory(prefix="zeline-backend-") as directory:
        environment.update(HOME=directory, ZELINE_HOME=str(Path(directory) / "data"),
                           PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)], cwd=ROOT,
            env=environment, capture_output=True, text=True, timeout=20,
        )
    assert result.returncode == 0, result.stdout + result.stderr


def test_batch_reflection_add_has_same_automatic_expiry_as_legacy_add():
    _run_isolated('''
        import time
        from zeline.memory import MemoryStore, REFLECTION_TTL_SECONDS

        store = MemoryStore("test:reflection-batch")
        store.default_source = "reflection"
        before = time.time()
        assert store.add("legacy lesson").startswith("OK")
        result = store.apply([{"action": "add", "text": "batch lesson"}])
        assert result["success"], result
        after = time.time()
        records = {record["text"]: record for record in store.records()}
        for text in ("legacy lesson", "batch lesson"):
            record = records[text]
            assert record["source"] == "reflection"
            assert record["expires_at"] is not None, (text, record)
            assert before + REFLECTION_TTL_SECONDS <= record["expires_at"]
            assert record["expires_at"] <= after + REFLECTION_TTL_SECONDS

        store.default_source = "user"
        assert store.apply([{"action": "add", "text": "operator fact"}])["success"]
        operator = next(r for r in store.records() if r["text"] == "operator fact")
        assert operator["expires_at"] is None
    ''')


def test_batch_reflection_explicit_expiry_remains_authoritative():
    _run_isolated('''
        import time
        from zeline.memory import MemoryStore

        store = MemoryStore("test:reflection-expiry")
        store.default_source = "reflection"
        expiry = time.time() + 600
        result = store.apply([{"action": "add", "text": "short-lived lesson",
                               "expires_at": expiry}])
        assert result["success"], result
        assert store.records()[0]["expires_at"] == expiry
    ''')


def test_reflection_review_callback_failure_cleans_ticket_hooks_and_lock():
    _run_isolated('''
        import threading
        import time
        from types import SimpleNamespace
        from zeline import config
        from zeline.sessions import Session, SessionStore

        config.PERSIST_SESSIONS = False
        store = SessionStore()
        original_check = lambda: False
        executor_check = lambda: False
        tickets = []

        def reflect(min_tool_calls, on_review):
            assert min_tool_calls == 9
            on_review()
            raise AssertionError("review admission failure must stop work")

        agent = SimpleNamespace(_should_stop=original_check,
                                executor=SimpleNamespace(_should_stop=executor_check),
                                reflect=reflect)
        session = Session(agent=agent, lock=threading.Lock(), last_used=time.monotonic())
        store._sessions["test:review"] = session

        def fail_review(ticket):
            assert tickets == [ticket]
            assert session.active_turn is ticket
            assert not session.lock.acquire(blocking=False)
            raise RuntimeError("observer failed")

        assert store.reflect("test:review", min_tool_calls=9,
                             on_ticket=tickets.append, on_review=fail_review) is None
        assert len(tickets) == 1
        assert tickets[0].finished.is_set()
        assert session.active_turn is None
        assert not session.running
        assert agent._should_stop is original_check
        assert agent.executor._should_stop is executor_check
        assert session.lock.acquire(blocking=False)
        session.lock.release()
    ''')
