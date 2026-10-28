"""Owner-message to native reverse-engineer response acceptance coverage."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from unittest import TestCase, mock

from zeline import tools
from zeline.gateways import telegram


class _NativeSession:
    def __init__(self, workspace: Path) -> None:
        self.executor = tools.ToolExecutor(
            "telegram:7387183839", "workspace", workspace
        )
        self.received = None
        self.finished = threading.Event()

    def send(self, *, identity, text, tool_profile, on_tool, on_tool_result,
             on_iteration, on_narration, **_kwargs):
        self.received = (identity, text, tool_profile)
        on_iteration(1, 1)
        on_narration("Inspecting the safe fixture with the native engine.")
        arguments = {"action": "inspect", "path": "target.bin"}
        on_tool("reverse_engineer", arguments)
        raw = self.executor.run("reverse_engineer", arguments)
        on_tool_result("reverse_engineer", arguments, raw)
        self.finished.set()
        result = json.loads(raw)
        return f"native status={result['status']} evidence={result['evidence_id']}"


class TelegramReverseE2ETests(TestCase):
    def test_owner_update_reaches_native_tool_and_returns_response(self):
        with tempfile.TemporaryDirectory(prefix="telegram-reverse-e2e-") as temp:
            root = Path(temp)
            state = root / "state"
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "target.bin").write_bytes(b"SAFE_STATIC_FIXTURE")
            session = _NativeSession(workspace)
            calls = []

            def api(_api, method, **payload):
                calls.append((method, payload))
                return {"ok": True, "result": {"message_id": len(calls)}}

            update = {
                "update_id": 7001,
                "message": {
                    "message_id": 42,
                    "chat": {"id": 7387183839},
                    "text": "inspect target.bin",
                },
            }
            with mock.patch.dict(os.environ, {"ZELINE_HOME": str(state)}), \
                    mock.patch.object(telegram, "_api_call", side_effect=api), \
                    mock.patch.object(telegram, "_LiveStatus"), \
                    mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
                    mock.patch.object(telegram, "_start_reflection"):
                telegram._dispatch_update(
                    "https://api.telegram.org/bot[REDACTED]", "[REDACTED]", session,
                    update,
                    allowed=[7387183839],
                    tool_profile="workspace",
                    stop_event=threading.Event(),
                )
                self.assertTrue(session.finished.wait(5), "agent worker did not finish")

            self.assertEqual(
                session.received,
                ("telegram:7387183839", "inspect target.bin", "workspace"),
            )
            messages = [payload for method, payload in calls if method == "sendMessage"]
            self.assertTrue(messages, calls)
            final = messages[-1]
            self.assertIn("native status=ok", final["text"])
            self.assertIn("evidence=zev_", final["text"])
            self.assertEqual(final["reply_to_message_id"], 42)
            self.assertEqual(final["chat_id"], 7387183839)
            self.assertEqual((workspace / "target.bin").read_bytes(), b"SAFE_STATIC_FIXTURE")


if __name__ == "__main__":
    import unittest

    unittest.main()
