"""Tests for zeline.routing (per-turn model routing).

Covers: deterministic classification per category, precedence rules,
RouterConfig from env / config-file dict (env always wins), resolve()
fail-closed behaviour, the agent.py send() hook (payload model, usage
accounting, narration, per-turn cleanup), and a subprocess check that
zeline.routing imports without pulling in zeline.agent.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import routing  # noqa: E402  (stdlib-only module, no heavy imports)

ROUTING_ENV_VARS = [
    "ZELINE_ROUTING_ENABLED",
    "ZELINE_ROUTE_CODE",
    "ZELINE_ROUTE_QUICK",
    "ZELINE_ROUTE_REASONING",
    "ZELINE_ROUTE_RESEARCH",
    "ZELINE_ROUTE_LONG_CONTEXT",
]


class RoutingEnvBase(unittest.TestCase):
    """Strips routing env vars so tests never leak into each other."""

    def setUp(self) -> None:
        self._saved_env = {name: os.environ.get(name) for name in ROUTING_ENV_VARS}
        for name in ROUTING_ENV_VARS:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name in ROUTING_ENV_VARS:
            if self._saved_env[name] is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = self._saved_env[name]


class ClassifyTests(RoutingEnvBase):
    def test_code_fence(self):
        self.assertEqual(
            routing.classify("```python\nprint('halo')\n```\njelaskan ini", []),
            "code",
        )

    def test_code_keyword(self):
        self.assertEqual(
            routing.classify("ada traceback error di fungsi login", []), "code"
        )

    def test_code_extension(self):
        self.assertEqual(
            routing.classify("buka file server.py lalu perbaiki", []), "code"
        )

    def test_code_bare_filenames(self):
        # dockerfile/makefile are extensionless filenames: the extension
        # regex (which requires a dot) can never match them — the bare
        # word regex must fire instead.
        self.assertEqual(
            routing.classify("tambahkan stage build di Dockerfile", []), "code"
        )
        self.assertEqual(
            routing.classify("perbaiki target deploy di makefile", []), "code"
        )
        # ...but a word merely containing the name as a substring is not a signal.
        self.assertEqual(
            routing.classify("dockerfileku mana", []), "default"
        )

    def test_code_tool_call_in_history(self):
        history = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "run_shell", "arguments": "{}"},
                    }
                ],
            }
        ]
        # "lanjutkan" alone would be quick — the tool history must win.
        self.assertEqual(routing.classify("lanjutkan", history), "code")

    def test_research_keywords(self):
        self.assertEqual(
            routing.classify("cari berita terbaru tentang harga bitcoin", []),
            "research",
        )

    def test_reasoning_needs_length_and_keyword(self):
        long_text = "Tolong lakukan analisis mendalam terhadap strategi " + "x" * 600
        self.assertEqual(routing.classify(long_text, []), "reasoning")

    def test_reasoning_keyword_alone_in_short_text_is_not_enough(self):
        self.assertEqual(routing.classify("analisis", []), "default")

    def test_long_context_by_text_size(self):
        self.assertEqual(routing.classify("x" * 40_000, []), "long_context")

    def test_long_context_counts_history(self):
        history = [{"role": "user", "content": "y" * 40_000}]
        # "halo" alone would be quick — context size must take precedence.
        self.assertEqual(routing.classify("halo", history), "long_context")

    def test_quick_greeting(self):
        self.assertEqual(routing.classify("halo", []), "quick")
        self.assertEqual(routing.classify("Hai, apa kabar?", []), "quick")

    def test_quick_simple_factual(self):
        self.assertEqual(routing.classify("jam berapa sekarang?", []), "quick")

    def test_ambiguous_is_default(self):
        self.assertEqual(
            routing.classify("bisakah kamu membantuku memahami sesuatu yang penting?", []),
            "default",
        )

    def test_precedence_code_beats_quick(self):
        self.assertEqual(routing.classify("halo, tolong debug kode ini", []), "code")

    def test_precedence_long_context_beats_code(self):
        self.assertEqual(routing.classify("```\n" + "x" * 40_000, []), "long_context")

    def test_precedence_research_beats_reasoning(self):
        text = "tolong bandingkan dan buat analisis strategi " + "x" * 600
        self.assertEqual(routing.classify(text, []), "research")

    def test_non_string_and_empty_input(self):
        self.assertEqual(routing.classify(None, None), "default")
        self.assertEqual(routing.classify("", []), "default")
        self.assertEqual(routing.classify("   ", []), "default")

    def test_non_ascii(self):
        self.assertEqual(
            routing.classify("Tolong perbaiki def hitung_total() ini 🙏", []),
            "code",
        )
        self.assertEqual(routing.classify("Terima kasih banyak 🙏", []), "quick")

    def test_indonesian_ambiguous_words_are_not_code_signals(self):
        # "kode" (promo code), "fungsi" (purpose), "program" (TV/government
        # programme) are ordinary Indonesian words — not code signals on
        # their own. Regression tests for false-positive routing.
        self.assertEqual(
            routing.classify("minta kode promo terbaru dong", []), "research"
        )
        self.assertEqual(
            routing.classify("apa fungsi vitamin C untuk tubuh?", []), "default"
        )
        self.assertEqual(
            routing.classify("info program bantuan pemerintah", []), "default"
        )

    def test_debug_keyword_signals_code(self):
        self.assertEqual(routing.classify("tolong debug kode ini", []), "code")

    def test_malformed_history_never_crashes(self):
        self.assertEqual(
            routing.classify("halo", ["junk", None, {"role": "user"}, {}]), "quick"
        )
        self.assertEqual(routing.classify("halo", "not-a-list"), "quick")


class RouterConfigTests(RoutingEnvBase):
    def test_from_env_disabled_by_default(self):
        cfg = routing.RouterConfig.from_env("main-model")
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.default_model, "main-model")
        self.assertEqual(cfg.routes, {})

    def test_from_env_truthy_variants(self):
        for value in ("1", "true", "TRUE", "yes", "on", " On "):
            os.environ["ZELINE_ROUTING_ENABLED"] = value
            self.assertTrue(
                routing.RouterConfig.from_env().enabled, f"value={value!r}"
            )

    def test_from_env_falsy_variants(self):
        for value in ("0", "false", "no", "off", "", "maybe"):
            os.environ["ZELINE_ROUTING_ENABLED"] = value
            self.assertFalse(
                routing.RouterConfig.from_env().enabled, f"value={value!r}"
            )

    def test_from_env_routes(self):
        os.environ["ZELINE_ROUTING_ENABLED"] = "1"
        os.environ["ZELINE_ROUTE_CODE"] = "code-model"
        os.environ["ZELINE_ROUTE_QUICK"] = "  quick-model  "
        cfg = routing.RouterConfig.from_env()
        self.assertTrue(cfg.enabled)
        self.assertEqual(
            cfg.routes, {"code": "code-model", "quick": "quick-model"}
        )

    def test_from_dict_basic(self):
        cfg = routing.RouterConfig.from_dict(
            {"enabled": True, "routes": {"code": "c", "research": "r"}},
            default_model="d",
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.default_model, "d")
        self.assertEqual(cfg.routes, {"code": "c", "research": "r"})

    def test_from_dict_ignores_unknown_categories(self):
        cfg = routing.RouterConfig.from_dict(
            {"enabled": True, "routes": {"code": "c", "fancy": "x", "CODE": "y"}}
        )
        self.assertEqual(cfg.routes, {"code": "c"})

    def test_from_dict_malformed_input_never_crashes(self):
        for bad in (None, "nope", 42, {"routes": "nope"}, {"routes": None}):
            cfg = routing.RouterConfig.from_dict(bad)
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.routes, {})
        # None route value is skipped, not stored.
        cfg = routing.RouterConfig.from_dict({"routes": {"code": None}})
        self.assertEqual(cfg.routes, {})

    def test_env_wins_over_dict_for_enabled(self):
        cfg = routing.RouterConfig.from_dict({"enabled": True})
        os.environ["ZELINE_ROUTING_ENABLED"] = "0"
        cfg.apply_env()
        self.assertFalse(cfg.enabled)

    def test_env_wins_over_dict_for_routes(self):
        cfg = routing.RouterConfig.from_dict(
            {"enabled": True, "routes": {"code": "file-code", "quick": "file-quick"}}
        )
        os.environ["ZELINE_ROUTE_CODE"] = "env-code"
        os.environ["ZELINE_ROUTE_QUICK"] = ""  # explicit empty unsets the route
        cfg.apply_env()
        self.assertEqual(cfg.routes, {"code": "env-code"})

    def test_apply_env_absent_keeps_file_values(self):
        cfg = routing.RouterConfig.from_dict(
            {"enabled": True, "routes": {"code": "file-code"}}
        )
        cfg.apply_env()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.routes, {"code": "file-code"})


class ResolveTests(RoutingEnvBase):
    def test_disabled_returns_default_unrouted(self):
        cfg = routing.RouterConfig(enabled=False, default_model="d")
        decision = routing.resolve("```python\nx=1\n```", [], cfg, "d")
        self.assertEqual(decision.category, "default")
        self.assertEqual(decision.model, "d")
        self.assertFalse(decision.routed)
        self.assertIn("disabled", decision.reason)

    def test_routed_when_category_has_route(self):
        cfg = routing.RouterConfig(
            enabled=True, default_model="d", routes={"quick": "q-model"}
        )
        decision = routing.resolve("halo", [], cfg, "d")
        self.assertEqual(decision.category, "quick")
        self.assertEqual(decision.model, "q-model")
        self.assertTrue(decision.routed)

    def test_enabled_but_no_route_for_category_falls_back(self):
        cfg = routing.RouterConfig(
            enabled=True, default_model="d", routes={"quick": "q-model"}
        )
        decision = routing.resolve("tolong debug kode ini", [], cfg, "d")
        self.assertEqual(decision.category, "code")
        self.assertEqual(decision.model, "d")
        self.assertFalse(decision.routed)

    def test_default_model_falls_back_to_config(self):
        cfg = routing.RouterConfig(
            enabled=True, default_model="cfg-model", routes={"quick": "q-model"}
        )
        decision = routing.resolve("halo", [], cfg, "")
        self.assertEqual(decision.model, "q-model")
        self.assertTrue(decision.routed)
        plain = routing.resolve("tolong debug kode ini", [], cfg, "")
        self.assertEqual(plain.model, "cfg-model")
        self.assertFalse(plain.routed)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        import json as _json

        self.text = _json.dumps(payload)
        self.status_code = status_code
        self.ok = status_code < 400
        self.encoding = "utf-8"


class AgentHookTests(RoutingEnvBase):
    """Integration: send() -> routing decision -> provider payload."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self._old_env = {
            key: os.environ.get(key)
            for key in ("ZELINE_HOME", "ZELINE_API_KEY", "ZELINE_BASE_URL", "ZELINE_MODEL")
        }
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
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for module_name in list(sys.modules):
            if module_name == "zeline" or module_name.startswith("zeline."):
                sys.modules.pop(module_name, None)
        self.temp.cleanup()
        super().tearDown()

    def _reply(self, text="Halo juga!", usage=True):
        payload = {"choices": [{"message": {"role": "assistant", "content": text}}]}
        if usage:
            payload["usage"] = {"prompt_tokens": 10, "completion_tokens": 5}
        return FakeResponse(payload)

    def _send(self, agent, text, **kwargs):
        with mock.patch.object(
            self.agent_module.requests, "post", return_value=self._reply()
        ) as post:
            reply = agent.send(text, **kwargs)
        payload = post.call_args[1]["json"]
        return reply, payload

    def test_disabled_by_default_payload_uses_configured_model(self):
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        reply, payload = self._send(agent, "halo")
        self.assertEqual(reply, "Halo juga!")
        # Behaviour identical to before routing existed.
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["model"], agent.model)
        self.assertIsNone(agent._turn_model)
        self.assertIsNotNone(agent.last_route_decision)
        self.assertFalse(agent.last_route_decision.routed)

    def test_routed_turn_uses_route_model_and_narrates_once(self):
        os.environ["ZELINE_ROUTING_ENABLED"] = "1"
        os.environ["ZELINE_ROUTE_QUICK"] = "quick-model"
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        narrations: list[str] = []
        reply, payload = self._send(agent, "halo", on_narration=narrations.append)
        self.assertEqual(payload["model"], "quick-model")
        self.assertEqual(len(narrations), 1)
        self.assertIn("quick-model", narrations[0])
        # Per-turn state must not leak into the next turn.
        self.assertIsNone(agent._turn_model)
        decision = agent.last_route_decision
        self.assertTrue(decision.routed)
        self.assertEqual(decision.category, "quick")
        self.assertEqual(decision.model, "quick-model")

    def test_routed_turn_records_usage_under_route_model(self):
        os.environ["ZELINE_ROUTING_ENABLED"] = "1"
        os.environ["ZELINE_ROUTE_QUICK"] = "quick-model"
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        store = mock.Mock()
        agent._usage_store_cache = store
        self._send(agent, "halo")
        store.record.assert_called_once()
        self.assertEqual(store.record.call_args[0][0], "quick-model")

    def test_enabled_but_unrouted_category_uses_default_model_silently(self):
        os.environ["ZELINE_ROUTING_ENABLED"] = "1"
        os.environ["ZELINE_ROUTE_QUICK"] = "quick-model"
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        narrations: list[str] = []
        reply, payload = self._send(
            agent, "tolong debug kode ini", on_narration=narrations.append
        )
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(narrations, [])
        self.assertIsNone(agent._turn_model)
        self.assertFalse(agent.last_route_decision.routed)
        self.assertEqual(agent.last_route_decision.category, "code")

    def test_config_file_section_is_honoured(self):
        self.agent_module.config.config["routing"] = {
            "enabled": True,
            "routes": {"quick": "file-model"},
        }
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        reply, payload = self._send(agent, "halo")
        self.assertEqual(payload["model"], "file-model")
        self.assertTrue(agent.last_route_decision.routed)

    def test_env_disables_file_section(self):
        self.agent_module.config.config["routing"] = {
            "enabled": True,
            "routes": {"quick": "file-model"},
        }
        os.environ["ZELINE_ROUTING_ENABLED"] = "0"
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        reply, payload = self._send(agent, "halo")
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(agent.last_route_decision.routed)

    def test_turn_model_cleared_even_on_provider_error(self):
        os.environ["ZELINE_ROUTING_ENABLED"] = "1"
        os.environ["ZELINE_ROUTE_QUICK"] = "quick-model"
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        with mock.patch.object(
            self.agent_module.requests,
            "post",
            side_effect=self.agent_module.requests.exceptions.ConnectionError(),
        ):
            with self.assertRaises(self.agent_module.ZelineError):
                agent.send("halo")
        self.assertIsNone(agent._turn_model)

    def test_system_prompt_excluded_from_classification(self):
        # messages[0] is the system prompt (tens of thousands of chars) and
        # must never push a turn into long_context by itself — send() passes
        # only messages[1:] to the classifier.
        os.environ["ZELINE_ROUTING_ENABLED"] = "1"
        os.environ["ZELINE_ROUTE_LONG_CONTEXT"] = "big-model"
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        agent.messages[0]["content"] += "x" * 40_000
        reply, payload = self._send(agent, "halo")
        self.assertEqual(agent.last_route_decision.category, "quick")
        self.assertFalse(agent.last_route_decision.routed)
        self.assertEqual(payload["model"], "test-model")

    def test_routing_failure_falls_back_to_default_model(self):
        # A routing crash must never break the turn: default model is used.
        agent = self.agent_module.Zeline(identity="telegram:123", tool_profile="safe")
        with mock.patch.object(
            self.agent_module.routing, "resolve", side_effect=RuntimeError("boom")
        ):
            reply, payload = self._send(agent, "halo")
        self.assertEqual(reply, "Halo juga!")
        self.assertEqual(payload["model"], "test-model")
        self.assertIsNone(agent._turn_model)
        self.assertIsNone(agent.last_route_decision)


class NoCircularImportTests(unittest.TestCase):
    def test_routing_imports_without_agent(self):
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(SOURCE_ROOT)!r}); "
            "import zeline.routing; "
            "assert 'zeline.agent' not in sys.modules, "
            "'zeline.routing must not pull in zeline.agent'; "
            "print('routing-ok')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(SOURCE_ROOT),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        self.assertIn("routing-ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
