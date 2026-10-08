"""M8: drain supervisor on the production ``agent.send()`` path.

The audit (M8) noted that ``TestAgentDrain`` only *mirrored* the drain order
manually -- no test called the real ``Zeline.send()`` and proved a completed
worker's completion block lands in that turn's provider payload. This file
closes that gap: it spawns a real, fast-finishing worker on the supervisor
for the agent's identity, then calls the REAL ``send()`` (provider mocked)
and asserts the worker's completion block appears in the turn's ephemeral
context inside the provider payload -- and that a second turn sees nothing
(the drain is exactly-once and turn-ephemeral, never persisted to history).

Only new test files may be added by this task: this module touches no
production code and no existing test.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


class FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)
        self.status_code = 200
        self.ok = True
        self.encoding = "utf-8"

    def json(self):
        return json.loads(self.text)


class SendDrainBase(unittest.TestCase):
    IDENTITY = "test:m8-send-drain"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_env = {
            key: os.environ.get(key)
            for key in ("ZELINE_HOME", "ZELINE_API_KEY", "ZELINE_BASE_URL", "ZELINE_MODEL")
        }
        os.environ["ZELINE_HOME"] = str(self.home)
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
        os.environ["ZELINE_MODEL"] = "test-model"
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.agent_mod = importlib.import_module("zeline.agent")
        self.supervisor_mod = importlib.import_module("zeline.supervisor")
        # Plain-JSON path, mirroring tests/test_agent.py.
        self.agent_mod.config.STREAM_RESPONSES = False

        # Provider mock: always a final content answer, no tool calls.
        # Records every payload so the test can inspect what the turn sent.
        self.provider_calls: list[dict] = []
        self._provider_lock = threading.Lock()
        patcher = mock.patch.object(
            self.agent_mod.requests, "post", side_effect=self._fake_post
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self._pools: list = []

    def tearDown(self) -> None:
        for pool in self._pools:
            try:
                pool.shutdown(timeout=10)
            except Exception:
                pass
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    # -- helpers ---------------------------------------------------------
    def _fake_post(self, url, **kwargs):
        with self._provider_lock:
            self.provider_calls.append(kwargs.get("json") or {})
        return FakeResponse(
            {"choices": [{"message": {"role": "assistant", "content": "Baik, siap."}}]}
        )

    def _wait_for(self, fn, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if fn():
                return True
            time.sleep(0.05)
        return bool(fn())

    def _spawn_finished_worker(self, identity: str) -> tuple[str, str]:
        """A real spawn that finishes fast, leaving an undelivered event.

        Uses the same module-level supervisor registry ``send()`` looks up,
        so the production drain path in ``send()`` finds the worker.
        """
        pool = self.supervisor_mod.get_supervisor(identity)
        self._pools.append(pool)
        pool.set_task_runner(lambda task, grants, wid: "ringkasan penting")
        wid = pool.spawn("tugas latar m8")
        done = self._wait_for(
            lambda: (pool.get_status(wid) or {}).get("status") == "done"
        )
        self.assertTrue(done, "background worker did not finish before the turn")
        return wid, identity

    def _last_user_text(self, payload: dict) -> str:
        users = [
            str(msg.get("content", ""))
            for msg in payload.get("messages", [])
            if msg.get("role") == "user"
        ]
        self.assertTrue(users, "payload has no user message")
        return users[-1]

    def _payload_text(self, payload: dict) -> str:
        return "\n".join(
            str(msg.get("content", "")) for msg in payload.get("messages", [])
        )


class TestSendDrainsWorkerCompletions(SendDrainBase):
    """M8: the real send() drains completed workers into the turn's context."""

    def test_completed_worker_block_appears_in_turn_payload(self):
        wid, identity = self._spawn_finished_worker(self.IDENTITY)
        agent = self.agent_mod.Zeline(identity=identity, tool_profile="safe")

        reply = agent.send("halo")

        self.assertEqual(reply, "Baik, siap.")
        self.assertEqual(len(self.provider_calls), 1)
        user_text = self._last_user_text(self.provider_calls[0])
        # The production drain path (bind + drain_completion_block, then
        # appended to the turn's ephemeral skill context) put the worker's
        # completion block into this turn's provider payload.
        self.assertIn(wid, user_text)
        self.assertIn("Pekerja", user_text)
        self.assertIn("selesai", user_text)
        self.assertIn("ringkasan penting", user_text)
        self.assertIn("tugas latar m8", user_text)

    def test_drain_is_exactly_once_and_not_persisted(self):
        wid, identity = self._spawn_finished_worker("test:m8-send-drain-once")
        agent = self.agent_mod.Zeline(identity=identity, tool_profile="safe")

        agent.send("halo")
        self.assertIn(wid, self._last_user_text(self.provider_calls[0]))

        # Second turn on the same agent: the event was already delivered, so
        # the completion block must not appear anywhere in the new payload --
        # not in the fresh user message, and not resurrected from history.
        agent.send("halo lagi")
        self.assertEqual(len(self.provider_calls), 2)
        second_text = self._payload_text(self.provider_calls[1])
        self.assertNotIn(wid, second_text)
        self.assertNotIn("ringkasan penting", second_text)


if __name__ == "__main__":
    unittest.main()
