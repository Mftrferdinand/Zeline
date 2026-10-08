"""Regression tests for corrupt timestamps in zeline.goals.

M5: one corrupt ``created_at``/``updated_at`` value must not collapse the
whole read — the field falls back to 0.0 and the goal survives.

HOME isolation follows tests/test_goals.py: ZELINE_HOME points at a temp
dir and zeline.* modules are reloaded so config.DATA_DIR is test-local.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


def fresh_goals(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    return importlib.import_module("zeline.goals")


class GoalsTimestampBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = os.environ.get("ZELINE_HOME")
        self.goals = fresh_goals(self.home)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def write_raw(self, identity: str, entries: list) -> None:
        key = self.goals._key(identity)
        path = self.home / "goals" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries), encoding="utf-8")

    def valid_entry(self, gid: str, **overrides):
        entry = {
            "id": gid,
            "title": f"Goal {gid}",
            "target": "target",
            "progress": 10,
            "deadline": None,
            "milestones": [],
            "status": "active",
            "created_at": 1700000000.0,
            "updated_at": 1700000001.0,
        }
        entry.update(overrides)
        return entry


class TestCorruptTimestamps(GoalsTimestampBase):
    """M5: a corrupt created_at/updated_at falls back to 0.0, reads survive."""

    def test_corrupt_created_at_string_falls_back(self):
        identity = "test:ts-corrupt"
        self.write_raw(
            identity,
            [
                self.valid_entry("g1", created_at="kemarin"),
                self.valid_entry("g2"),
            ],
        )
        goals = self.goals.list_goals(identity)
        self.assertEqual(len(goals), 2, "the corrupt goal must survive, not vanish")
        corrupt = next(g for g in goals if g["id"] == "g1")
        self.assertEqual(corrupt["created_at"], 0.0)
        good = next(g for g in goals if g["id"] == "g2")
        self.assertEqual(good["created_at"], 1700000000.0)

    def test_get_goal_with_corrupt_created_at(self):
        identity = "test:ts-get"
        self.write_raw(identity, [self.valid_entry("g1", created_at="kemarin")])
        goal = self.goals.get_goal(identity, "g1")
        self.assertEqual(goal["title"], "Goal g1")
        self.assertEqual(goal["created_at"], 0.0)

    def test_prompt_block_survives_corrupt_created_at(self):
        identity = "test:ts-prompt"
        self.write_raw(identity, [self.valid_entry("g1", created_at="kemarin")])
        block = self.goals.prompt_block_goals(identity)
        self.assertIn("Goal g1", block)

    def test_corrupt_updated_at_falls_back(self):
        identity = "test:ts-updated"
        self.write_raw(
            identity,
            [self.valid_entry("g1", updated_at={"bukan": "epoch"})],
        )
        goals = self.goals.list_goals(identity)
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["updated_at"], 0.0)

    def test_corrupt_timestamp_none_and_missing(self):
        identity = "test:ts-none"
        self.write_raw(
            identity,
            [
                self.valid_entry("g1", created_at=None),
                self.valid_entry("g2", updated_at=None),
            ],
        )
        goals = self.goals.list_goals(identity)
        self.assertEqual(len(goals), 2)
        self.assertEqual(goals[0]["created_at"], 0.0)
        self.assertEqual(goals[1]["updated_at"], 0.0)

    def test_update_goal_still_works_with_corrupt_timestamp(self):
        identity = "test:ts-update"
        self.write_raw(identity, [self.valid_entry("g1", created_at="kemarin")])
        goal, _note = self.goals.update_goal(identity, "g1", progress=50)
        self.assertEqual(goal["progress"], 50)
        # The repaired write keeps a sane timestamp afterwards.
        again = self.goals.get_goal(identity, "g1")
        self.assertGreater(again["updated_at"], 0.0)


if __name__ == "__main__":
    unittest.main()
