"""M7: end-to-end proof that worker grants are enforced by a REAL worker.

The audit (M7) noted that grant wiring was only ever tested with an injected
fake runner -- no test proved that a tool outside a worker's grants is DENIED
by a real worker. This file closes that gap: it spawns a worker through the
production DEFAULT runner (a real ``zeline.agent.Zeline`` sub-agent, a real
``ToolExecutor``, a real ``GrantApprovalPolicy``) with only the provider
mocked at ``requests.post``. The mocked model requests two out-of-grant
tools (``write_file``, ``run_shell``) and one in-grant tool (``read_file``).

The tests assert:

- ``write_file`` and ``run_shell`` come back as DENIALS (the exact gate
  message), never executions;
- ``read_file`` (inside the grants) really executes and returns real content;
- the denied tools never reach ``ToolExecutor._dispatch`` and their side
  effects never happen on disk.

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

READ_GRANTS = {"tools": ["read_file"], "risk": ["read"]}


class FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)
        self.status_code = 200
        self.ok = True
        self.encoding = "utf-8"

    def json(self):
        return json.loads(self.text)


class WorkerGrantsE2EBase(unittest.TestCase):
    """Fresh zeline modules per test; provider mocked; dispatch spied on."""

    IDENTITY = "test:m7-e2e"

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
        self.supervisor_mod = importlib.import_module("zeline.supervisor")
        self.agent_mod = importlib.import_module("zeline.agent")
        self.tools_mod = importlib.import_module("zeline.tools")
        # Legacy tests exercise the non-stream JSON path; pin it off so the
        # plain-JSON FakeResponse is used (mirrors tests/test_agent.py).
        self.agent_mod.config.STREAM_RESPONSES = False

        self.workspace = self.home / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "catatan.txt").write_text(
            "ISI-CATATAN-M7\n", encoding="utf-8"
        )

        # Spy on the single execution point: every real tool execution in
        # this process (including the worker thread's) passes through
        # ToolExecutor._dispatch. A denied call never gets there.
        self.dispatched: list[str] = []
        self._dispatch_lock = threading.Lock()
        real_dispatch = self.tools_mod.ToolExecutor._dispatch

        def spy_dispatch(executor_self, name, args):
            with self._dispatch_lock:
                self.dispatched.append(str(name))
            return real_dispatch(executor_self, name, args)

        self.tools_mod.ToolExecutor._dispatch = spy_dispatch  # type: ignore[method-assign]
        self.addCleanup(
            setattr, self.tools_mod.ToolExecutor, "_dispatch", real_dispatch
        )

        # Stateful provider mock: the first call returns the scripted tool
        # calls, every later call returns final content. Stateful (not a
        # fixed side_effect list) so a retry/anti-loop path can never blow
        # up with StopIteration and flake the test.
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
            call_no = len(self.provider_calls)
        if call_no == 1:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-write-1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {"path": "jahat.txt", "content": "pwned"}
                            ),
                        },
                    },
                    {
                        "id": "call-shell-1",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps({"command": "touch pwned.txt"}),
                        },
                    },
                    {
                        "id": "call-read-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "catatan.txt"}),
                        },
                    },
                ],
            }
        else:
            message = {"role": "assistant", "content": "Ringkasan: selesai."}
        return FakeResponse({"choices": [{"message": message}]})

    def _wait_for(self, fn, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if fn():
                return True
            time.sleep(0.05)
        return bool(fn())

    def _spawn_read_only_worker(self, identity: str, grants):
        """Spawn through the DEFAULT runner (real Zeline); wait for done."""
        pool = self.supervisor_mod.get_supervisor(identity)
        self._pools.append(pool)
        pool.bind(profile="full", workspace=str(self.workspace), depth=0)
        wid = pool.spawn("tugas m7: baca catatan lalu tulis file", grants=grants)
        done = self._wait_for(
            lambda: (pool.get_status(wid) or {}).get("status") == "done"
        )
        self.assertTrue(done, "worker with the default runner did not finish")
        return pool, wid

    def _tool_results(self) -> dict[str, str]:
        """Map tool_call_id -> result content from the provider payload that
        carried the tool results back (the model's view of the denial)."""
        payloads = [
            payload
            for payload in self.provider_calls
            if any(
                msg.get("role") == "tool"
                for msg in payload.get("messages", [])
            )
        ]
        self.assertEqual(
            len(payloads), 1, "expected exactly one tool-result round trip"
        )
        return {
            msg.get("tool_call_id"): str(msg.get("content", ""))
            for msg in payloads[0]["messages"]
            if msg.get("role") == "tool"
        }


class TestWorkerGrantsEnforcedE2E(WorkerGrantsE2EBase):
    """M7: the default runner's real sub-agent enforces its grants."""

    def test_out_of_grant_tools_denied_in_grant_tool_runs(self):
        pool, wid = self._spawn_read_only_worker(
            self.IDENTITY, {"tools": ["read_file"], "risk": ["read"]}
        )
        status = pool.get_status(wid)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["attempts"], 1, "worker should succeed first try")
        result = pool.get_result(wid)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["result"], "Ringkasan: selesai.")

        # The model SAW denials for both out-of-grant tools: the exact gate
        # message, byte-identical to what the choke point returns.
        tool_results = self._tool_results()
        deny_write = self.tools_mod._approval_denied_message("write_file")
        deny_shell = self.tools_mod._approval_denied_message("run_shell")
        self.assertEqual(tool_results.get("call-write-1"), deny_write)
        self.assertEqual(tool_results.get("call-shell-1"), deny_shell)
        # Belt and braces: literal markers, independent of the helper.
        self.assertTrue(
            tool_results["call-write-1"].startswith(
                "ERROR: tool 'write_file' was not approved"
            )
        )
        self.assertIn("was not executed", tool_results["call-shell-1"])

        # The in-grant tool really executed and returned real file content.
        self.assertIn("ISI-CATATAN-M7", tool_results.get("call-read-1", ""))

        # Enforcement, not paper: denied tools never reached _dispatch, and
        # their side effects never happened on disk.
        self.assertEqual(self.dispatched, ["read_file"])
        self.assertFalse((self.workspace / "jahat.txt").exists())
        self.assertFalse((self.workspace / "pwned.txt").exists())

    def test_default_read_only_grants_also_deny_writes(self):
        # grants=None normalizes to the strict read-only default; the same
        # denial must hold without an explicit declaration.
        pool, wid = self._spawn_read_only_worker(
            "test:m7-e2e-default", grants=None
        )
        status = pool.get_status(wid)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["status"], "done")
        tool_results = self._tool_results()
        self.assertTrue(
            tool_results["call-write-1"].startswith(
                "ERROR: tool 'write_file' was not approved"
            )
        )
        self.assertTrue(
            tool_results["call-shell-1"].startswith(
                "ERROR: tool 'run_shell' was not approved"
            )
        )
        self.assertIn("ISI-CATATAN-M7", tool_results.get("call-read-1", ""))
        self.assertEqual(self.dispatched, ["read_file"])
        self.assertFalse((self.workspace / "jahat.txt").exists())
        self.assertFalse((self.workspace / "pwned.txt").exists())


if __name__ == "__main__":
    unittest.main()
