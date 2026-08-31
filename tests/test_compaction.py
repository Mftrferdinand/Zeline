"""Evicted turns must leave a trace: an on-disk transcript plus a digest.

These tests pin the contract that ``_trim_history`` no longer loses work. The
expensive part of the old design (an LLM summarization call on every trim) is
deliberately absent, so every assertion here runs offline and deterministically.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from zeline import compaction, config


def _user(text):
    return {"role": "user", "content": text}


def _tool_turn(name, args, result="ok"):
    call = {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": result},
        {"role": "assistant", "content": "done"},
    ]


class CompactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        patcher = mock.patch.object(config, "DATA_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_nothing_to_record_returns_none(self):
        self.assertIsNone(compaction.compact([], "cli:local"))
        empty = [{"role": "assistant", "content": "   "}]
        self.assertIsNone(compaction.compact(empty, "cli:local"))
        self.assertFalse(compaction.archive_root().exists())

    def test_archive_holds_the_verbatim_text_of_evicted_turns(self):
        dropped = [_user("tolong tulis config baru"), *_tool_turn("write_file", {"path": "app/config.py"})]
        summary = compaction.compact(dropped, "telegram:aes")
        assert summary is not None
        files = list(compaction.archive_root().glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("tolong tulis config baru", text)
        self.assertIn("write_file", text)
        self.assertIn("app/config.py", text)

    def test_digest_names_the_user_asks_and_the_files_touched(self):
        dropped = [
            _user("pakai postgres, bukan sqlite"),
            *_tool_turn("write_file", {"path": "db/schema.sql"}),
            _user("sekarang tambah index"),
            *_tool_turn("edit_file", {"path": "db/schema.sql"}),
            *_tool_turn("run_shell", {"command": "pytest"}),
        ]
        summary = compaction.compact(dropped, "cli:local")
        assert summary is not None
        content = summary["content"]
        self.assertIn(compaction.DIGEST_MARKER, content)
        self.assertIn("pakai postgres, bukan sqlite", content)
        self.assertIn("sekarang tambah index", content)
        self.assertIn("db/schema.sql", content)
        self.assertIn("run_shell", content)
        # A file touched twice is listed once, not duplicated.
        self.assertEqual(content.count("db/schema.sql"), 1)

    def test_digest_points_at_the_archive_so_detail_is_recoverable(self):
        dropped = [_user("ingat keputusan ini"), *_tool_turn("write_file", {"path": "notes.md"})]
        summary = compaction.compact(dropped, "cli:local")
        assert summary is not None
        archived = next(iter(compaction.archive_root().glob("*.md")))
        self.assertIn(str(archived), summary["content"])
        self.assertIn("read_file(", summary["content"])

    def test_digest_is_bounded_and_cannot_itself_overflow_the_window(self):
        dropped = []
        for index in range(40):
            dropped.append(_user(f"permintaan panjang nomor {index} " + "x" * 4_000))
            dropped.extend(_tool_turn("write_file", {"path": f"file{index}.py"}))
        summary = compaction.compact(dropped, "cli:local")
        assert summary is not None
        self.assertLessEqual(
            len(summary["content"]),
            compaction.MAX_DIGEST_CHARS + 100,
            "digest must stay small enough that compaction is a net win",
        )

    def test_repeated_compaction_does_not_digest_its_own_digest(self):
        first = compaction.compact(
            [_user("langkah satu"), *_tool_turn("write_file", {"path": "a.py"})],
            "cli:local",
        )
        assert first is not None
        second = compaction.compact(
            [first, _user("langkah dua"), *_tool_turn("write_file", {"path": "b.py"})],
            "cli:local",
        )
        assert second is not None
        self.assertEqual(second["content"].count(compaction.DIGEST_MARKER), 1)
        self.assertIn("langkah dua", second["content"])
        self.assertNotIn("langkah satu", second["content"])

    def test_archive_path_is_readable_but_arbitrary_paths_are_not(self):
        compaction.compact([_user("halo"), *_tool_turn("write_file", {"path": "x.py"})], "cli:local")
        archived = next(iter(compaction.archive_root().glob("*.md")))
        self.assertTrue(compaction.is_archive_path(archived))
        self.assertFalse(compaction.is_archive_path(Path("/etc/passwd")))
        self.assertFalse(compaction.is_archive_path(Path("/data/local/tmp/evil.md")))

    def test_identity_with_path_separators_cannot_escape_the_archive_dir(self):
        summary = compaction.compact(
            [_user("halo"), *_tool_turn("write_file", {"path": "x.py"})],
            "../../etc/passwd",
        )
        assert summary is not None
        files = list(compaction.archive_root().glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertTrue(compaction.is_archive_path(files[0]))

    def test_unwritable_archive_still_yields_a_usable_digest(self):
        with mock.patch.object(compaction, "archive", return_value=None):
            summary = compaction.compact(
                [_user("keputusan penting"), *_tool_turn("write_file", {"path": "y.py"})],
                "cli:local",
            )
        assert summary is not None
        self.assertIn("keputusan penting", summary["content"])
        self.assertNotIn("read_file(", summary["content"])

    def test_malformed_tool_arguments_do_not_break_the_digest(self):
        broken = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "write_file", "arguments": "{not json"}}],
        }
        summary = compaction.compact([_user("coba"), broken], "cli:local")
        assert summary is not None
        self.assertIn("write_file", summary["content"])

    def test_prune_removes_stale_transcripts(self):
        compaction.compact([_user("lama"), *_tool_turn("write_file", {"path": "old.py"})], "cli:old")
        stale = next(iter(compaction.archive_root().glob("*.md")))
        import os

        os.utime(stale, (1, 1))
        self.assertEqual(compaction.prune(), 1)
        self.assertFalse(stale.exists())

    def test_prune_keeps_the_transcript_it_just_wrote(self):
        summary = compaction.compact(
            [_user("baru"), *_tool_turn("write_file", {"path": "new.py"})], "cli:new"
        )
        assert summary is not None
        files = list(compaction.archive_root().glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn(str(files[0]), summary["content"])


class TrimHistoryIntegrationTests(unittest.TestCase):
    """The agent path: trimming must inject the digest, not silently drop.

    ``zeline.agent`` reads provider config at import time, so this fixture
    mirrors ``tests/test_agent.py``: point ZELINE_HOME at a temp dir and
    reimport the package, then talk to *that* module's compaction instance.
    """

    def setUp(self) -> None:
        import importlib
        import os
        import sys

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = {
            key: os.environ.get(key)
            for key in ("ZELINE_HOME", "ZELINE_API_KEY", "ZELINE_BASE_URL", "ZELINE_MODEL")
        }
        os.environ["ZELINE_HOME"] = str(Path(self._tmp.name) / "state")
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
        os.environ["ZELINE_MODEL"] = "test-model"
        for module_name in list(sys.modules):
            if module_name == "zeline" or module_name.startswith("zeline."):
                sys.modules.pop(module_name, None)
        self.agent_module = importlib.import_module("zeline.agent")
        self.agent_module.config.STREAM_RESPONSES = False
        self.compaction = self.agent_module.compaction
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        import os
        import sys

        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for module_name in list(sys.modules):
            if module_name == "zeline" or module_name.startswith("zeline."):
                sys.modules.pop(module_name, None)

    def _agent(self):
        return self.agent_module.Zeline(identity="telegram:trim", tool_profile="safe")

    def test_trim_injects_a_digest_naming_the_evicted_file_writes(self):
        compaction = self.compaction
        agent = self._agent()
        system = agent.messages[0]
        history = [system]
        for index in range(30):
            history.append(_user(f"permintaan {index} " + "d" * 1_500))
            history.extend(_tool_turn("write_file", {"path": f"src/mod{index}.py"}))
        agent.messages = history
        agent._trim_history()

        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertEqual(agent.messages[1]["role"], "user")
        self.assertIn(compaction.DIGEST_MARKER, agent.messages[1]["content"])
        self.assertIn("src/mod0.py", agent.messages[1]["content"])
        self.assertEqual(len(list(compaction.archive_root().glob("*.md"))), 1)

    def test_second_trim_leaves_exactly_one_digest_at_the_front(self):
        compaction = self.compaction
        agent = self._agent()
        system = agent.messages[0]

        def grow():
            for index in range(30):
                agent.messages.append(_user(f"lagi {index} " + "e" * 1_500))
                agent.messages.extend(_tool_turn("write_file", {"path": f"p{index}.py"}))

        agent.messages = [system]
        grow()
        agent._trim_history()
        grow()
        agent._trim_history()

        digests = [m for m in agent.messages if compaction.is_digest(m)]
        self.assertEqual(len(digests), 1)
        self.assertIs(digests[0], agent.messages[1])

    def test_trim_below_the_limit_adds_no_digest(self):
        compaction = self.compaction
        agent = self._agent()
        system = agent.messages[0]
        agent.messages = [system, _user("singkat"), {"role": "assistant", "content": "ok"}]
        agent._trim_history()
        self.assertEqual(len(agent.messages), 3)
        self.assertFalse(any(compaction.is_digest(m) for m in agent.messages))
        self.assertFalse(compaction.archive_root().exists())

    def test_archive_failure_does_not_break_the_turn(self):
        compaction = self.compaction
        agent = self._agent()
        system = agent.messages[0]
        history = [system]
        for index in range(30):
            history.append(_user(f"permintaan {index} " + "f" * 1_500))
            history.extend(_tool_turn("write_file", {"path": f"q{index}.py"}))
        agent.messages = history
        with mock.patch.object(compaction, "compact", side_effect=OSError("disk full")):
            agent._trim_history()
        self.assertEqual(agent.messages[0]["role"], "system")
        self.assertEqual(agent.messages[1]["role"], "user")


if __name__ == "__main__":
    unittest.main()
