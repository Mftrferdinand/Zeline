"""Kontrak Zeline App — schema respons, auth, sanitasi, event, streaming, cancel.

Test yang menulis data mengarahkan ``ZELINE_APP_DATA_DIR`` ke direktori temporer;
tanpa itu fixture mengotori ``~/.zeline/app`` milik user.

unittest, bukan pytest: CI menjalankan ``python -m unittest discover`` dan pytest
tidak terpasang di sana.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.gateways import GATEWAYS, zeline_app
from zeline.gateways import zeline_app_runtime as runtime
from zeline import app_auth, app_data, tool_events

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENT_MOBILE_FIELDS = (
    "id", "name", "avatar", "description", "provider_id", "model",
    "system_instructions", "enabled_tools", "enabled_skills", "memory_enabled",
    "created_at",
)
SECRET_FIELDS = ("api_key", "secret", "token", "credential")


class IsolatedAppData(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.data_dir = self._dir.name
        patcher = mock.patch.dict(os.environ, {"ZELINE_APP_DATA_DIR": self.data_dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)


class ContractSurfaceTests(unittest.TestCase):
    def test_gateway_registered_and_labelled(self):
        self.assertEqual(zeline_app.info().get("label"), "Zeline App")
        self.assertIn("zeline_app", GATEWAYS)

    def test_api_prefix_and_version_agree(self):
        """A client pins /api/v1; the prefix and the version field must not drift."""
        self.assertEqual(zeline_app.API_PREFIX, f"/api/v{zeline_app.API_VERSION}")

    def test_one_account_holds_one_agent(self):
        """Separate chat rooms are separate sessions, not separate agents."""
        self.assertEqual(zeline_app.MAX_AGENTS, 1)


class AuthTests(unittest.TestCase):
    def test_token_round_trip_and_foreign_secret_rejected(self):
        secret = secrets.token_bytes(32)
        token = app_auth.generate_token("agent_test", secret=secret)
        payload = app_auth.verify_token(token, secret=secret)
        self.assertIsNotNone(payload)
        self.assertIn("agent_ref", payload or {})
        self.assertIn("scope", payload or {})
        self.assertIsNone(app_auth.verify_token(token, secret=secrets.token_bytes(32)))

    def test_sanitize_strips_every_secret_field(self):
        agent = {"id": "a1", "name": "Agent", "provider": "9router",
                 "api_key": "secret123", "token": "t", "credential": "x"}
        clean = app_auth.sanitize_for_client(agent)
        for field in ("api_key", "token", "credential"):
            self.assertNotIn(field, clean)
        self.assertEqual(clean.get("id"), "a1")


class AgentModelTests(IsolatedAppData):
    def test_profile_has_every_mobile_field_and_no_secret(self):
        """The app renders these fields directly; a missing one is a blank row."""
        agent = app_data.add_agent({
            "name": "Coding",
            "avatar": "icon-coding",
            "description": "Coding agent",
            "provider_id": "p1",
            "model": "kimi",
            "system_instructions": "Be concise.",
            "enabled_tools": ["run_shell"],
            "enabled_skills": ["marketanalysis"],
            "memory_enabled": True,
        })
        for field in AGENT_MOBILE_FIELDS:
            self.assertIn(field, agent, f"field {field} hilang dari agent profile")
        clean = app_auth.sanitize_for_client(agent)
        for bad in SECRET_FIELDS:
            self.assertNotIn(bad, clean, f"secret {bad} bocor ke client")

    def test_roster_marks_only_configured_agent_tokens_connected(self):
        """Connected means this gateway explicitly stores the profile's Agent Token."""
        agent = app_data.add_agent({"name": "Live", "provider_id": "p1", "model": "m1"})
        app_data.add_session({"id": "sess_live", "session_id": "sess_live",
                              "agent_id": agent["id"]})
        self.assertIn(agent["id"],
                      zeline_app._connected_agent_ids(None, [agent["agent_api_token"]]))
        self.assertEqual(zeline_app._connected_agent_ids(None, []), set())

    def test_sessions_are_isolated(self):
        first = app_data.add_session({"agent_id": "a1", "title": "Chat 1"})
        second = app_data.add_session({"agent_id": "a2", "title": "Chat 2"})
        self.assertNotEqual(first["session_id"], second["session_id"])


class ProviderMaskingTests(unittest.TestCase):
    def test_refs_never_expose_a_full_key(self):
        for ref in app_data.list_provider_refs():
            self.assertNotIn("api_key", ref)
            hint = ref.get("api_key_hint")
            if hint is not None:
                self.assertTrue("••" in str(hint) or len(str(hint)) <= 4)


class ToolEventSchemaTests(unittest.TestCase):
    def test_event_has_every_contract_field(self):
        event = tool_events.make_tool_event(
            "tool.started", "a", "s", tool_name="run_shell", payload={"command": "ls"})
        for key in ("event", "agent_id", "session_id", "tool_name", "status",
                    "payload", "gateway"):
            self.assertIn(key, event, f"field {key} hilang dari event")


class StreamRegistryTests(unittest.TestCase):
    def test_steer_only_accepts_an_active_session(self):
        sid = "sess_steer_contract"
        self.assertFalse(runtime.stream_steer(sid, "focus here"))
        runtime.stream_start(sid, "stream_contract")
        self.addCleanup(runtime.stream_finish, sid)
        self.assertTrue(runtime.stream_steer(sid, "focus here"))
        self.assertEqual(runtime.take_stream_steer(sid), "focus here")
        self.assertIsNone(runtime.take_stream_steer(sid))

    def test_cancel_is_idempotent_and_scoped_to_an_active_stream(self):
        """The app taps stop twice; the second tap must not claim a fresh cancel."""
        sid = "sess_cancel_contract"
        self.assertEqual(runtime.stream_cancel(sid), (False, None))
        runtime.stream_start(sid, "stream_cancel_contract")
        self.assertEqual(runtime.stream_cancel(sid), (True, "stream_cancel_contract"))
        self.assertTrue(runtime.is_cancelled(sid))
        runtime.stream_finish(sid)
        self.assertEqual(runtime.stream_cancel(sid), (False, None))
        self.assertFalse(runtime.is_cancelled(sid))


class StreamingOverrideTests(IsolatedAppData):
    def test_gateway_agent_runtime_forces_streaming(self):
        """SSE promises assistant.delta and mid-stream cancel; both need streaming.

        ``agent.stream`` is a CLI preference. With it off, this gateway emitted a
        single delta at the very end and cancel could not land until the blocking
        provider request returned (measured 113-211s), because nothing read in a
        loop that could check the flag. The gateway overrides it per instance
        rather than reading the global.
        """
        from zeline import config

        agent = {"id": "agent_stream", "model": "m1", "provider_id": "",
                 "system_instructions": ""}
        sid = "sess_stream_contract"
        self.addCleanup(runtime.drop_session_runtime, sid)
        with mock.patch.object(config, "STREAM_RESPONSES", False):
            instance = runtime.get_agent_runtime(sid, agent, "safe")
            self.assertTrue(instance.stream_responses)
            self.assertTrue(instance._streaming_enabled())

    def test_cli_instances_still_follow_the_global_preference(self):
        """The override must not silently turn streaming on for everyone else."""
        from zeline import config
        from zeline.agent import Zeline

        with mock.patch.object(config, "STREAM_RESPONSES", False):
            self.assertFalse(Zeline(identity="cli:test")._streaming_enabled())
        with mock.patch.object(config, "STREAM_RESPONSES", True):
            self.assertTrue(Zeline(identity="cli:test")._streaming_enabled())


class SessionIdPathGuardTests(IsolatedAppData):
    """Session ids name files on disk, so they are validated in the storage layer.

    The HTTP router already matches `[\\w-]+`, but a guard that lives only in the
    router is inherited-broken by every new caller (CLI, migration, another
    gateway). CodeQL flagged the same thing as py/path-injection: the taint
    reaches `open()`.
    """

    def test_traversal_and_separators_are_rejected(self):
        for bad in ("../etc/passwd", "a/b", "/abs", "..", "", "x" * 129, "sess id", "sess\x00"):
            with self.subTest(session_id=bad):
                with self.assertRaises(ValueError):
                    runtime.safe_session_id(bad)

    def test_normal_ids_pass_through_unchanged(self):
        for good in ("sess_ab4d2f1d", "SESS-123", "a", "x" * 128):
            with self.subTest(session_id=good):
                self.assertEqual(runtime.safe_session_id(good), good)

    def test_writes_land_inside_the_data_dir(self):
        runtime.append_message(
            "sess_guard", runtime.new_message("sess_guard", "agent_guard", "assistant", "hi"))
        written = Path(self.data_dir) / "messages" / "sess_guard.json"
        self.assertTrue(written.exists())

    def test_a_rejected_id_cannot_write_anywhere(self):
        with self.assertRaises(ValueError):
            runtime.append_message(
                "../escaped", runtime.new_message("../escaped", "a", "assistant", "hi"))
        self.assertFalse((Path(self.data_dir).parent / "escaped.json").exists())

    def test_reading_a_rejected_id_is_empty_not_an_error(self):
        """A lookup with a hostile id is a 404 case, not a 500 case."""
        self.assertEqual(runtime.load_messages("../etc/passwd"), [])

    def test_dropping_a_rejected_id_still_clears_the_registry(self):
        runtime.stream_start("../escaped", "stream_x")
        runtime.drop_session_runtime("../escaped")
        self.assertFalse(runtime.is_cancelled("../escaped"))

    def test_resolved_paths_stay_under_the_data_dir(self):
        """Containment is asserted on the resolved path, not inferred from the regex."""
        root = Path(self.data_dir).resolve()
        for sid in ("sess_ab4d2f1d", "SESS-123", "a"):
            with self.subTest(session_id=sid):
                for path in (runtime._history_path(sid), runtime._messages_path(sid)):
                    self.assertTrue(str(path.resolve()).startswith(str(root) + os.sep),
                                    f"{path} escaped {root}")


class HistoryTests(IsolatedAppData):
    def test_saved_message_ids_are_deduplicated_against_real_history(self):
        sid = "sess_saved_contract"
        runtime.append_message(sid, runtime.new_message(sid, "agent_save", "assistant", "Important"))
        valid = runtime.load_messages(sid)[0]["id"]
        requested = [valid, valid, "missing"]
        filtered = list(dict.fromkeys(value for value in requested if value in {valid}))
        self.assertEqual(filtered, [valid])


class SystemInfoTests(unittest.TestCase):
    def test_no_ip_address_is_ever_reported(self):
        info = zeline_app._system_info()
        for key in ("kind", "os", "arch", "python", "zeline_version", "runtime", "online"):
            self.assertIn(key, info, f"missing {key} in /system")
        self.assertNotIn("ip", info)
        blob = json.dumps(info).lower()
        self.assertIsNone(re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", blob))


class ClientExampleTests(unittest.TestCase):
    def test_example_consumes_the_streaming_contract(self):
        path = REPO_ROOT / "examples" / "zeline_app_client.py"
        self.assertTrue(path.exists(), "client example tidak ada")
        content = path.read_text(encoding="utf-8")
        for marker in ("/auth/login", "assistant.delta", "tool.started", "cancel"):
            self.assertIn(marker, content, f"example tidak menunjukkan {marker}")


if __name__ == "__main__":
    unittest.main()
