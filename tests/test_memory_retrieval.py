"""Scored memory retrieval (Fase 1).

``prompt_block`` used to inject the ENTIRE memory store into the system prompt
every turn. These tests pin the retrieval upgrade:

- ``score_text``: the shared scoring primitive —
  keyword_match × recency × confidence — behaves sanely on concrete examples;
- ``MemoryStore.retrieve`` / ``LessonsStore.retrieve`` rank candidates and
  return top-K, excluding keyword mismatches and expired records;
- ``prompt_block(query=...)`` injects only the top-K hits in the EXACT legacy
  format (byte-identical rendering to a store holding just those hits);
- fallback is REAL, not decorative: empty/blank query, zero-hit query, and a
  forced exception mid-retrieval all yield the old inject-everything block;
- the per-turn wiring: an agent ``send()`` builds the system prompt from the
  just-arrived user message, so each turn sees only relevant memory.

Fake identity ``telegram:111222333`` throughout — no real chat IDs.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

FAKE_IDENTITY = "telegram:111222333"


def _fresh(home: Path):
    """Reimport zeline.memory + zeline.lessons bound to a temp ZELINE_HOME."""
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    lessons = importlib.import_module("zeline.lessons")
    memory = importlib.import_module("zeline.memory")
    return memory, lessons


class _TempHomeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("ZELINE_HOME")
        # Hermetic: test-test di file ini mengunci KONTRAK jalur keyword
        # (perilaku retrieval sebelum hybrid ada). Paksa embedding mati supaya
        # hasilnya deterministik baik fastembed terinstal maupun tidak —
        # perilaku hybrid sendiri diuji di tests/test_hybrid_retrieval.py.
        self.old_embeddings_enabled = os.environ.get("ZELINE_EMBEDDINGS_ENABLED")
        os.environ["ZELINE_EMBEDDINGS_ENABLED"] = "0"
        self.memory, self.lessons = _fresh(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        if self.old_embeddings_enabled is None:
            os.environ.pop("ZELINE_EMBEDDINGS_ENABLED", None)
        else:
            os.environ["ZELINE_EMBEDDINGS_ENABLED"] = self.old_embeddings_enabled
        self.temp.cleanup()


class ScoringFormulaTests(_TempHomeTestCase):
    """score_text = keyword_match × recency × confidence, on concrete examples."""

    NOW = 1_700_000_000.0

    def test_full_match_fresh_confident_scores_one(self):
        score = self.memory.score_text("budi meeting", "meeting dengan budi", self.NOW, 1.0, self.NOW)
        self.assertAlmostEqual(score, 1.0)

    def test_no_keyword_overlap_scores_zero_despite_fresh_confident(self):
        score = self.memory.score_text("budi meeting", "suka kopi tubruk", self.NOW, 1.0, self.NOW)
        self.assertEqual(score, 0.0)

    def test_query_without_significant_tokens_scores_zero(self):
        # "ya dan atau" are all stopwords → no significant token → 0.
        score = self.memory.score_text("ya dan atau", "budi meeting", self.NOW, 1.0, self.NOW)
        self.assertEqual(score, 0.0)

    def test_recency_halves_score_after_one_half_life(self):
        half_life = self.memory.RETRIEVAL_RECENCY_HALF_LIFE
        fresh = self.memory.score_text("budi", "budi", self.NOW, 1.0, self.NOW)
        old = self.memory.score_text("budi", "budi", self.NOW - half_life, 1.0, self.NOW)
        self.assertAlmostEqual(old, fresh * 0.5)

    def test_confidence_scales_score_linearly(self):
        full = self.memory.score_text("budi", "budi", self.NOW, 1.0, self.NOW)
        low = self.memory.score_text("budi", "budi", self.NOW, 0.6, self.NOW)
        self.assertAlmostEqual(low, full * 0.6)

    def test_partial_keyword_overlap_is_proportional(self):
        # 1 of 2 query tokens present → keyword_match = 0.5.
        score = self.memory.score_text("budi meeting", "budi", self.NOW, 1.0, self.NOW)
        self.assertAlmostEqual(score, 0.5)

    def test_matching_is_case_insensitive(self):
        score = self.memory.score_text("BUDI", "budi", self.NOW, 1.0, self.NOW)
        self.assertAlmostEqual(score, 1.0)


class MemoryRetrieveTests(_TempHomeTestCase):
    def _seed_mixed_store(self) -> object:
        """One relevant+fresh+confident fact, one relevant+old+weak, noise, expired."""
        store = self.memory.MemoryStore(FAKE_IDENTITY)
        now = time.time()
        records = [
            {
                "text": "User likes black coffee",
                "kind": "fact",
                "source": "user",
                "confidence": 1.0,
                "created_at": now,
                "expires_at": None,
            },
            {
                "text": "Budi is user's old colleague",
                "kind": "fact",
                "source": "reflection",
                "confidence": 0.6,
                "created_at": now - 60 * 86400,
                "expires_at": None,
            },
            {
                "text": "Meeting dengan Budi hari Jumat jam 10",
                "kind": "fact",
                "source": "user",
                "confidence": 1.0,
                "created_at": now,
                "expires_at": None,
            },
            {
                "text": "Budi deadline kemarin",
                "kind": "fact",
                "source": "user",
                "confidence": 1.0,
                "created_at": now,
                "expires_at": now - 1,  # expired → must never be retrieved
            },
        ]
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps(records), encoding="utf-8")
        return store

    def test_retrieve_ranks_relevant_fresh_confident_first(self):
        store = self._seed_mixed_store()
        hits = store.retrieve("kapan meeting dengan budi?")
        texts = [hit["text"] for hit in hits]
        # Relevant + fresh + confident wins outright.
        self.assertEqual(texts[0], "Meeting dengan Budi hari Jumat jam 10")
        # Relevant but old + weak confidence still qualifies, ranked lower.
        self.assertIn("Budi is user's old colleague", texts)
        # Noise (no keyword overlap) and expired records are excluded.
        self.assertNotIn("User likes black coffee", texts)
        self.assertNotIn("Budi deadline kemarin", texts)

    def test_retrieve_never_raises_and_returns_empty_for_blank_query(self):
        store = self._seed_mixed_store()
        self.assertEqual(store.retrieve(""), [])
        self.assertEqual(store.retrieve("   "), [])

    def test_retrieve_empty_store_returns_empty(self):
        store = self.memory.MemoryStore("telegram:111222333-empty")
        self.assertEqual(store.retrieve("budi"), [])

    def test_retrieve_caps_at_top_k(self):
        store = self.memory.MemoryStore("telegram:111222333-many")
        for i in range(12):
            store.add(f"Budi note number {i}")
        hits = store.retrieve("budi")
        self.assertEqual(len(hits), self.memory.RETRIEVAL_TOP_K)

    def test_retrieve_returns_empty_when_scoring_raises(self):
        """A mid-scoring exception degrades to [] — never propagates."""
        store = self._seed_mixed_store()
        with mock.patch.object(self.memory, "score_text", side_effect=RuntimeError("boom")):
            self.assertEqual(store.retrieve("meeting budi"), [])


class MemoryPromptBlockRetrievalTests(_TempHomeTestCase):
    def _seed(self, identity: str = FAKE_IDENTITY) -> object:
        store = self.memory.MemoryStore(identity)
        store.add("Meeting dengan Budi hari Jumat jam 10")
        store.add("User likes black coffee")
        store.default_source = "reflection"
        store.add("User often corrects UI spacing")
        return store

    def test_query_injects_only_hits_in_identical_format(self):
        store = self._seed()
        # A store holding ONLY the hit renders the legacy block; the retrieved
        # block must be byte-identical to it.
        single = self.memory.MemoryStore("telegram:111222333-single")
        single.add("Meeting dengan Budi hari Jumat jam 10")
        self.assertEqual(
            store.prompt_block(query="meeting budi jumat"),
            single.prompt_block(),
        )

    def test_empty_query_falls_back_to_full_injection(self):
        store = self._seed()
        expected = store.prompt_block()
        self.assertEqual(store.prompt_block(query=""), expected)
        self.assertEqual(store.prompt_block(query="   "), expected)
        self.assertEqual(store.prompt_block(query=None), expected)

    def test_zero_hit_query_falls_back_to_full_injection(self):
        store = self._seed()
        self.assertEqual(store.prompt_block(query="zzz qqq www"), store.prompt_block())

    def test_forced_retrieval_exception_falls_back_to_full_injection(self):
        """The fallback is real: even a raising retrieve() yields the old block."""
        store = self._seed()
        expected = store.prompt_block()
        with mock.patch.object(
            self.memory.MemoryStore, "retrieve", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(store.prompt_block(query="meeting budi"), expected)

    def test_empty_store_still_renders_empty_block(self):
        store = self.memory.MemoryStore("telegram:111222333-vacant")
        self.assertEqual(store.prompt_block(query="budi"), "")
        self.assertEqual(store.prompt_block(), "")


class LessonsRetrieveTests(_TempHomeTestCase):
    def _seed(self, db_name: str, identity: str = FAKE_IDENTITY) -> object:
        store = self.lessons.LessonsStore(path=Path(self.temp.name) / db_name)
        store.record_failure(identity, "edit_file", {"path": "x.py"}, "ERROR: not found")
        store.record_fix(identity, "edit_file", "x.py", "use absolute path")
        return store

    def test_retrieve_ranks_matching_lesson_and_excludes_mismatch(self):
        store = self._seed("lessons-a.db")
        store.record_failure(FAKE_IDENTITY, "run_shell", {"command": "ls /nope"}, "ERROR: timeout")
        store.record_fix(FAKE_IDENTITY, "run_shell", "ls", "retry with shorter timeout")
        hits = store.retrieve(FAKE_IDENTITY, "edit_file not found")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["tool"], "edit_file")

    def test_retrieve_empty_store_returns_empty(self):
        store = self.lessons.LessonsStore(path=Path(self.temp.name) / "lessons-empty.db")
        self.assertEqual(store.retrieve(FAKE_IDENTITY, "edit_file"), [])

    def test_prompt_block_query_renders_identical_format(self):
        store = self._seed("lessons-b.db")
        store.record_failure(FAKE_IDENTITY, "run_shell", {"command": "ls /nope"}, "ERROR: timeout")
        store.record_fix(FAKE_IDENTITY, "run_shell", "ls", "retry with shorter timeout")
        # Same lesson alone in another DB renders the legacy block; the
        # retrieved block must be byte-identical.
        single = self._seed("lessons-single.db")
        self.assertEqual(
            store.prompt_block(FAKE_IDENTITY, query="edit_file not found"),
            single.prompt_block(FAKE_IDENTITY),
        )

    def test_prompt_block_fallbacks_match_legacy_behavior(self):
        store = self._seed("lessons-c.db")
        expected = store.prompt_block(FAKE_IDENTITY)
        self.assertEqual(store.prompt_block(FAKE_IDENTITY, query=""), expected)
        self.assertEqual(store.prompt_block(FAKE_IDENTITY, query="   "), expected)
        self.assertEqual(store.prompt_block(FAKE_IDENTITY, query=None), expected)
        self.assertEqual(store.prompt_block(FAKE_IDENTITY, query="zzz qqq www"), expected)

    def test_forced_retrieval_exception_falls_back_to_full_block(self):
        store = self._seed("lessons-d.db")
        expected = store.prompt_block(FAKE_IDENTITY)
        with mock.patch.object(
            self.lessons.LessonsStore, "retrieve", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(
                store.prompt_block(FAKE_IDENTITY, query="edit_file"), expected
            )

    def test_prompt_block_still_empty_without_resolved_lessons(self):
        store = self.lessons.LessonsStore(path=Path(self.temp.name) / "lessons-e.db")
        self.assertEqual(store.prompt_block(FAKE_IDENTITY, query="edit_file"), "")
        self.assertEqual(store.prompt_block(FAKE_IDENTITY), "")


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.text = json.dumps(payload)
        self.status_code = status_code
        self.ok = status_code < 400
        self.encoding = "utf-8"


class PerTurnRetrievalAgentTests(unittest.TestCase):
    """Realistic turns: each turn's system prompt carries only relevant memory."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("ZELINE_HOME")
        self.old_key = os.environ.get("ZELINE_API_KEY")
        self.old_base = os.environ.get("ZELINE_BASE_URL")
        self.old_model = os.environ.get("ZELINE_MODEL")
        # Hermetic seperti _TempHomeTestCase: skenario turn ini mengunci
        # kontrak jalur keyword — embedding dipaksa mati.
        self.old_embeddings_enabled = os.environ.get("ZELINE_EMBEDDINGS_ENABLED")
        os.environ["ZELINE_EMBEDDINGS_ENABLED"] = "0"
        os.environ["ZELINE_HOME"] = str(Path(self.temp.name) / "state")
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
        os.environ["ZELINE_MODEL"] = "test-model"
        for module_name in list(sys.modules):
            if module_name == "zeline" or module_name.startswith("zeline."):
                sys.modules.pop(module_name, None)
        self.agent_module = importlib.import_module("zeline.agent")
        self.agent_module.config.STREAM_RESPONSES = False

    def tearDown(self):
        for key, value in {
            "ZELINE_HOME": self.old_home,
            "ZELINE_API_KEY": self.old_key,
            "ZELINE_BASE_URL": self.old_base,
            "ZELINE_MODEL": self.old_model,
            "ZELINE_EMBEDDINGS_ENABLED": self.old_embeddings_enabled,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def _system_prompt_sent(self, post) -> str:
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"][0]["role"], "system")
        return payload["messages"][0]["content"]

    def test_each_turn_sees_only_relevant_memory(self):
        agent = self.agent_module.Zeline(identity=FAKE_IDENTITY, tool_profile="safe")
        agent.executor.memory.add("Meeting dengan Budi hari Jumat jam 10")
        agent.executor.memory.add("User's dog is named Bruno")
        agent.executor.memory.add("User likes black coffee")

        # Turn 1: about the meeting → only the Budi fact may enter the prompt.
        first = {"choices": [{"message": {"role": "assistant", "content": "Jumat jam 10."}}]}
        with mock.patch.object(
            self.agent_module.requests, "post", return_value=FakeResponse(first)
        ) as post:
            agent.send("kapan meeting sama budi?")
        system = self._system_prompt_sent(post)
        self.assertIn("Budi hari Jumat", system)
        self.assertNotIn("Bruno", system)
        self.assertNotIn("black coffee", system)

        # Turn 2: about the dog → only the Bruno fact may enter the prompt.
        second = {"choices": [{"message": {"role": "assistant", "content": "Bruno."}}]}
        with mock.patch.object(
            self.agent_module.requests, "post", return_value=FakeResponse(second)
        ) as post:
            agent.send("what is my dog's name?")
        system = self._system_prompt_sent(post)
        self.assertIn("Bruno", system)
        self.assertNotIn("Budi", system)
        self.assertNotIn("black coffee", system)

    def test_session_start_prompt_is_unchanged(self):
        """No user message yet → query is None → legacy full injection."""
        seeder = self.agent_module.Zeline(identity=FAKE_IDENTITY, tool_profile="safe")
        seeder.executor.memory.add("Meeting dengan Budi hari Jumat jam 10")
        seeder.executor.memory.add("User likes black coffee")
        agent = self.agent_module.Zeline(identity=FAKE_IDENTITY, tool_profile="safe")
        prompt = agent.messages[0]["content"]
        self.assertIn("Budi hari Jumat", prompt)
        self.assertIn("black coffee", prompt)


if __name__ == "__main__":
    unittest.main()
