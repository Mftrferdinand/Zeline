"""Regression: an interrupted latest request must survive a gateway restart."""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    sessions = importlib.import_module("zeline.sessions")
    persistence = importlib.import_module("zeline.session_store")
    return sessions, persistence


class _CrashingAgent:
    restored: list[dict] | None = None

    def __init__(self, **_kwargs):
        self.messages = [{"role": "system", "content": "fresh system"}]

    def load_history(self, messages: list[dict]) -> None:
        self.messages = messages
        type(self).restored = messages

    def export_history(self) -> list[dict]:
        return self.messages

    def send(self, _text: str, **_kwargs) -> str:
        raise RuntimeError("gateway process died during this turn")

    def force_cancel(self) -> None:
        pass


class RestartContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous_home = os.environ.get("ZELINE_HOME")
        self.addCleanup(self._restore_home)
        self.home = Path(self.tmp.name) / "zeline-home"
        self.sessions, self.persistence = fresh(self.home)

    def _restore_home(self) -> None:
        if self.previous_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.previous_home
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def test_latest_unfinished_request_is_hydrated_after_gateway_crash(self):
        """A later bare "lanjut" must see task four, not an older completed task."""
        identity = "telegram:restart-continuation"
        persistence = self.persistence.SessionPersistence(self.home / "sessions.db")
        persistence.save(
            identity,
            [
                {"role": "system", "content": "old system"},
                {"role": "user", "content": "task one: already completed"},
                {"role": "assistant", "content": "task one done"},
                {"role": "user", "content": "task three: already completed"},
                {"role": "assistant", "content": "task three done"},
            ],
            title="task one",
        )

        with mock.patch.object(self.sessions, "Zeline", _CrashingAgent):
            store = self.sessions.SessionStore(persistence=persistence)
            with self.assertRaisesRegex(RuntimeError, "process died"):
                store.send(identity, "task four: finish the deployment", "full")

            # Simulate a new gateway process rebuilding the session from SQLite.
            rebuilt = self.sessions.SessionStore(persistence=self.persistence.SessionPersistence(self.home / "sessions.db"))
            rebuilt.get_or_create(identity, "full")

        restored = _CrashingAgent.restored or []
        contents = [str(message.get("content", "")) for message in restored]
        self.assertIn("task four: finish the deployment", contents)
        self.assertEqual(contents[-2], "task four: finish the deployment")
        self.assertIn("[Zeline recovery]", contents[-1])


if __name__ == "__main__":
    unittest.main()
