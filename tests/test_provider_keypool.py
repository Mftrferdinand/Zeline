"""Unit test untuk provider API key pool (rotasi otomatis).

Menutup: KeyPool (bad/limited/sticky/rebuild), merge config api_key +
api_keys, override env ZELINE_API_KEYS, dan rotasi di _call_llm saat
401/403/429 — termasuk preservasi perilaku single-key.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(payload or {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        self.encoding = "utf-8"

    def close(self):
        pass


def _fresh_agent_module(env_extra=None):
    old = {k: os.environ.get(k) for k in ("ZELINE_HOME", "ZELINE_API_KEY", "ZELINE_API_KEYS", "ZELINE_BASE_URL", "ZELINE_MODEL")}
    tmp = tempfile.TemporaryDirectory()
    os.environ["ZELINE_HOME"] = str(Path(tmp.name) / "state")
    os.environ["ZELINE_API_KEY"] = "test-key"
    os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
    os.environ["ZELINE_MODEL"] = "test-model"
    os.environ.pop("ZELINE_API_KEYS", None)
    for k, v in (env_extra or {}).items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for module_name in list(sys.modules):
        if module_name == "zeline" or module_name.startswith("zeline."):
            sys.modules.pop(module_name, None)
    mod = importlib.import_module("zeline.agent")
    mod.config.STREAM_RESPONSES = False
    return mod, tmp, old


def _restore_env(old, tmp):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    tmp.cleanup()


class KeyPoolUnitTests(unittest.TestCase):
    def setUp(self):
        self.mod, self.tmp, self.old = _fresh_agent_module()

    def tearDown(self):
        _restore_env(self.old, self.tmp)

    def test_empty_pool_is_falsy(self):
        pool = self.mod.KeyPool([])
        self.assertFalse(pool)
        self.assertEqual(len(pool), 0)
        self.assertEqual(pool.live_keys(), [])

    def test_mark_bad_hides_key_until_ttl(self):
        pool = self.mod.KeyPool(["k1", "k2"])
        pool.mark_bad("k1")
        self.assertEqual(pool.live_keys(), ["k2"])
        # Setelah TTL lewat, key hidup lagi (relay flapping pulih).
        with mock.patch.object(self.mod.time, "time", return_value=self.mod.time.time() + 601):
            self.assertEqual(pool.live_keys(), ["k1", "k2"])

    def test_mark_limited_hides_key_for_cooldown(self):
        pool = self.mod.KeyPool(["k1", "k2"])
        pool.mark_limited("k1")
        self.assertEqual(pool.live_keys(), ["k2"])
        with mock.patch.object(self.mod.time, "time", return_value=self.mod.time.time() + 61):
            self.assertEqual(pool.live_keys(), ["k1", "k2"])

    def test_mark_good_is_sticky(self):
        pool = self.mod.KeyPool(["k1", "k2"])
        pool.mark_good("k2")
        self.assertEqual(pool.live_keys(), ["k2", "k1"])

    def test_rebuild_resets_state(self):
        pool = self.mod.KeyPool(["k1", "k2"])
        pool.mark_bad("k1")
        pool.mark_good("k2")
        pool.rebuild(["a", "b"])
        self.assertEqual(pool.live_keys(), ["a", "b"])

    def test_pool_from_config_single_key_first(self):
        cfg = self.mod.config
        with mock.patch.object(cfg, "API_KEYS", ["k2", "k3"], create=True):
            with mock.patch.object(cfg, "API_KEY", "k1", create=True):
                self.assertEqual(self.mod._pool_from_config(), ["k1", "k2", "k3"])
    def test_pool_from_config_falls_back_to_single_key(self):
        # Tanpa API_KEYS (config versi lama) → fallback ke API_KEY tunggal.
        cfg = self.mod.config
        saved = cfg.API_KEYS
        try:
            del cfg.API_KEYS
            with mock.patch.object(cfg, "API_KEY", "solo"):
                self.assertEqual(self.mod._pool_from_config(), ["solo"])
        finally:
            cfg.API_KEYS = saved


class ConfigPoolTests(unittest.TestCase):
    def test_provider_key_pool_merges_and_dedups(self):
        mod, tmp, old = _fresh_agent_module()
        try:
            merged = mod.config._provider_key_pool(
                {"api_key": "k1", "api_keys": ["k2", "k1", " ", "k3"]}
            )
            self.assertEqual(merged, ["k1", "k2", "k3"])
        finally:
            _restore_env(old, tmp)

    def test_env_api_keys_overrides_whole_pool(self):
        mod, tmp, old = _fresh_agent_module({"ZELINE_API_KEYS": "e1, e2,e1"})
        try:
            self.assertEqual(mod.config.API_KEYS, ["e1", "e2"])
            self.assertEqual(mod.config.API_KEY, "")
        finally:
            _restore_env(old, tmp)


class CallLlmRotationTests(unittest.TestCase):
    def setUp(self):
        self.mod, self.tmp, self.old = _fresh_agent_module()
        self.sleep_patch = mock.patch.object(self.mod.time, "sleep")
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        _restore_env(self.old, self.tmp)

    def _agent_with_keys(self, keys):
        agent = self.mod.Zeline(identity="cli:test", tool_profile="safe")
        agent._key_pool.rebuild(keys)
        return agent

    def _auth_of(self, call):
        return call.kwargs["headers"]["Authorization"]

    def test_401_rotates_to_next_key(self):
        agent = self._agent_with_keys(["bad1", "good2"])
        responses = [FakeResponse(401), FakeResponse(200)]
        with mock.patch.object(self.mod.requests, "post", side_effect=responses) as post:
            msg = agent._call_llm(use_tools=False)
        self.assertEqual(msg["content"], "ok")
        self.assertEqual(len(post.call_args_list), 2)
        self.assertEqual(self._auth_of(post.call_args_list[0]), "Bearer bad1")
        self.assertEqual(self._auth_of(post.call_args_list[1]), "Bearer good2")
        # bad1 dicoret: call berikutnya langsung pakai good2 (sticky).
        with mock.patch.object(self.mod.requests, "post", return_value=FakeResponse(200)) as post2:
            agent._call_llm(use_tools=False)
        self.assertEqual(self._auth_of(post2.call_args_list[0]), "Bearer good2")

    def test_429_rotates_without_burning_retry_on_same_key(self):
        agent = self._agent_with_keys(["limited1", "fresh2"])
        responses = [FakeResponse(429), FakeResponse(200)]
        with mock.patch.object(self.mod.requests, "post", side_effect=responses) as post:
            agent._call_llm(use_tools=False)
        self.assertEqual(len(post.call_args_list), 2)
        self.assertEqual(self._auth_of(post.call_args_list[0]), "Bearer limited1")
        self.assertEqual(self._auth_of(post.call_args_list[1]), "Bearer fresh2")

    def test_all_keys_401_raises_pool_message(self):
        agent = self._agent_with_keys(["dead1", "dead2"])
        with mock.patch.object(self.mod.requests, "post", return_value=FakeResponse(401)):
            with self.assertRaises(self.mod.ZelineError) as ctx:
                agent._call_llm(use_tools=False)
        self.assertIn("All API keys in the pool were rejected", str(ctx.exception))

    def test_single_key_401_still_raises_immediately(self):
        agent = self._agent_with_keys(["only"])
        with mock.patch.object(self.mod.requests, "post", return_value=FakeResponse(401)) as post:
            with self.assertRaises(self.mod.ZelineError):
                agent._call_llm(use_tools=False)
        self.assertEqual(len(post.call_args_list), 1)

    def test_single_key_429_keeps_backoff_retry(self):
        agent = self._agent_with_keys(["only"])
        responses = [FakeResponse(429), FakeResponse(200)]
        with mock.patch.object(self.mod.requests, "post", side_effect=responses) as post:
            msg = agent._call_llm(use_tools=False)
        self.assertEqual(msg["content"], "ok")
        self.assertEqual(len(post.call_args_list), 2)
        # Retry backoff tetap dipakai untuk single key.
        self.assertTrue(self.mod.time.sleep.called)

    def test_403_also_marks_key_bad(self):
        agent = self._agent_with_keys(["revoked", "spare"])
        responses = [FakeResponse(403), FakeResponse(200)]
        with mock.patch.object(self.mod.requests, "post", side_effect=responses):
            agent._call_llm(use_tools=False)
        self.assertNotIn("revoked", agent._key_pool.live_keys())

    def test_500_still_retries_same_key_with_backoff(self):
        agent = self._agent_with_keys(["k1", "k2"])
        responses = [FakeResponse(500), FakeResponse(200)]
        with mock.patch.object(self.mod.requests, "post", side_effect=responses) as post:
            agent._call_llm(use_tools=False)
        # 5xx = transient: key yang sama dicoba lagi (bukan rotasi).
        self.assertEqual(
            [self._auth_of(c) for c in post.call_args_list],
            ["Bearer k1", "Bearer k1"],
        )

    def test_no_keys_raises_setup_error(self):
        agent = self._agent_with_keys([])
        with self.assertRaises(self.mod.ZelineError) as ctx:
            agent._call_llm(use_tools=False)
        self.assertIn("zeline setup", str(ctx.exception))

    def test_api_key_property_stays_compatible(self):
        agent = self._agent_with_keys(["a", "b"])
        self.assertEqual(agent.api_key, "a")
        agent.api_key = "z"  # pola lama: assign langsung
        self.assertEqual(agent._key_pool.all_keys(), ["z"])
        self.assertEqual(agent.api_key, "z")


if __name__ == "__main__":
    unittest.main()
