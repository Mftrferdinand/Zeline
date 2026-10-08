"""Unit test untuk session-scoped approval cache.

Kontrak yang diuji:
- parse_verdict: "allow"/"allow once" -> once; "allow_session"/"allow sesi ini"
  -> session; segala hal lain (deny, timeout, cancel, kosong, None) -> deny.
- Cache key = (identity, nama tool): beda tool / beda identity tidak berbagi.
- Isolasi identitas: "cron:<id>" tidak bocor ke chat user; sub-agent "::sub"
  tidak berbagi dengan induk.
- clear_session_allows: hanya identity itu yang dibersihkan; terpanggil saat
  sesi berakhir (SessionStore.reset / stop / eviction).
- APPROVAL_OPTIONS: tepat tiga opsi, tanpa "allow selamanya".
- E2E via agent loop: "Allow sesi ini" -> panggilan kedua tool yang sama
  dalam sesi yang sama TIDAK memicu picker; sesi baru (identity lain) ->
  picker muncul lagi.

Catatan pola: modul zeline di-evict + re-import per test (fresh_modules,
seperti test_tool_risk) supaya state cache module-level tidak bocor antar
test dan supaya objek yang dipakai agent loop = objek yang di-assert.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_modules():
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    approvals = importlib.import_module("zeline.approvals")
    interaction = importlib.import_module("zeline.interaction")
    config = importlib.import_module("zeline.config")
    agent_module = importlib.import_module("zeline.agent")
    sessions = importlib.import_module("zeline.sessions")
    return approvals, interaction, config, agent_module, sessions


class ApprovalsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_env = dict(os.environ)
        os.environ["ZELINE_HOME"] = str(self.home / "state")
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
        os.environ["ZELINE_MODEL"] = "test-model"
        (
            self.approvals,
            self.interaction,
            self.config,
            self.agent_module,
            self.sessions,
        ) = fresh_modules()
        self.config.STREAM_RESPONSES = False
        self.workspace = self.home / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        try:
            self.interaction._PENDING.clear()
            self.interaction._CHANNELS.clear()
        except Exception:
            pass
        self._tmp.cleanup()


class ParseVerdictTests(ApprovalsBase):
    def test_once_variants(self):
        for verdict in ("allow", "Allow", " allow ", "allow once", "Allow Once", "ALLOW ONCE"):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.approvals.parse_verdict(verdict), "once")

    def test_session_variants(self):
        for verdict in (
            "allow_session",
            "Allow_Session",
            "allow sesi ini",
            "Allow Sesi Ini",
            " allow sesi ini ",
        ):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.approvals.parse_verdict(verdict), "session")

    def test_everything_else_is_deny(self):
        # Fail-closed: string error/timeout/cancel dari interaction.ask,
        # jawaban kosong, atau None tidak boleh meloloskan tool.
        for verdict in (
            "deny",
            "Deny",
            "DENY",
            "",
            "   ",
            None,
            "NO ANSWER: the user did not reply within 30s.",
            "CANCELLED: the user cancelled this question.",
            "(empty answer)",
            "yes",
            "ok",
            "allow forever",
            "allow always",
        ):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.approvals.parse_verdict(verdict), "deny")


class CacheScopeTests(ApprovalsBase):
    def test_grant_and_check_same_identity_tool(self):
        self.approvals.grant_session_allow("telegram:123", "run_shell")
        self.assertTrue(self.approvals.session_allowed("telegram:123", "run_shell"))

    def test_different_tool_not_allowed(self):
        self.approvals.grant_session_allow("telegram:123", "run_shell")
        self.assertFalse(self.approvals.session_allowed("telegram:123", "write_file"))

    def test_different_identity_not_allowed(self):
        self.approvals.grant_session_allow("telegram:123", "run_shell")
        self.assertFalse(self.approvals.session_allowed("telegram:456", "run_shell"))

    def test_cron_identity_isolated_from_chat(self):
        # Skenario rusak yang paling berbahaya: izin yang diberikan di chat
        # user bocor ke eksekusi cron (atau sebaliknya).
        self.approvals.grant_session_allow("cron:nightly", "run_shell")
        self.assertTrue(self.approvals.session_allowed("cron:nightly", "run_shell"))
        self.assertFalse(self.approvals.session_allowed("telegram:123", "run_shell"))
        self.approvals.grant_session_allow("telegram:123", "run_shell")
        self.assertTrue(self.approvals.session_allowed("telegram:123", "run_shell"))
        self.assertTrue(self.approvals.session_allowed("cron:nightly", "run_shell"))

    def test_subagent_identity_isolated_from_parent(self):
        self.approvals.grant_session_allow("telegram:123", "run_shell")
        self.assertFalse(self.approvals.session_allowed("telegram:123::sub1", "run_shell"))
        self.approvals.grant_session_allow("telegram:123::sub1", "run_shell")
        self.assertTrue(self.approvals.session_allowed("telegram:123::sub1", "run_shell"))

    def test_empty_identity_normalizes_to_cli_local(self):
        self.approvals.grant_session_allow("", "run_shell")
        self.assertTrue(self.approvals.session_allowed("cli:local", "run_shell"))
        self.assertTrue(self.approvals.session_allowed("", "run_shell"))

    def test_clear_only_affects_given_identity(self):
        self.approvals.grant_session_allow("telegram:123", "run_shell")
        self.approvals.grant_session_allow("telegram:123", "write_file")
        self.approvals.grant_session_allow("telegram:456", "run_shell")
        cleared = self.approvals.clear_session_allows("telegram:123")
        self.assertEqual(cleared, 2)
        self.assertFalse(self.approvals.session_allowed("telegram:123", "run_shell"))
        self.assertTrue(self.approvals.session_allowed("telegram:456", "run_shell"))
        self.assertEqual(self.approvals.clear_session_allows("telegram:123"), 0)

    def test_options_have_no_allow_forever(self):
        self.assertEqual(
            self.approvals.APPROVAL_OPTIONS,
            ("Allow once", "Allow sesi ini", "Deny"),
        )
        joined = " ".join(self.approvals.APPROVAL_OPTIONS).lower()
        self.assertNotIn("forever", joined)
        self.assertNotIn("always", joined)
        self.assertNotIn("selamanya", joined)


class FakeProviderResponse:
    def __init__(self, payload, status_code=200):
        import json

        self.text = json.dumps(payload)
        self.status_code = status_code
        self.ok = status_code < 400
        self.encoding = "utf-8"


class SessionAllowEndToEndTests(ApprovalsBase):
    """E2E lewat agent loop asli dengan provider di-mock.

    Operator menjawab "Allow sesi ini" pada run_shell pertama: tool jalan,
    dan run_shell kedua dalam sesi yang SAMA tidak memicu picker lagi.
    """

    def _shell_call_payload(self, marker):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call-{marker}",
                                "type": "function",
                                "function": {
                                    "name": "run_shell",
                                    "arguments": f'{{"command": "touch {marker}"}}',
                                },
                            }
                        ],
                    }
                }
            ],
        }

    def _final_payload(self):
        return {"choices": [{"message": {"role": "assistant", "content": "done."}}]}

    def _answerer(self, identity, answer, counter):
        def renderer(entry):
            counter["asks"] += 1
            counter["options"] = tuple(entry.options)
            return answer

        self.interaction.register_channel(identity, renderer)

    def test_session_allow_skips_second_picker(self):
        identity = "test:risk-session-allow"
        agent = self.agent_module.Zeline(
            identity=identity, tool_profile="full", workspace=str(self.workspace)
        )
        counter = {"asks": 0, "options": ()}
        self._answerer(identity, "Allow sesi ini", counter)
        marker_a = self.workspace / "SESSION_ALLOW_A"
        marker_b = self.workspace / "SESSION_ALLOW_B"
        post = mock.patch.object(
            self.agent_module.requests,
            "post",
            side_effect=[
                FakeProviderResponse(self._shell_call_payload("SESSION_ALLOW_A")),
                FakeProviderResponse(self._final_payload()),
                FakeProviderResponse(self._shell_call_payload("SESSION_ALLOW_B")),
                FakeProviderResponse(self._final_payload()),
            ],
        )
        with post:
            agent.send("jalankan perintah pertama")
            agent.send("jalankan perintah kedua")
        # Kedua tool jalan...
        self.assertTrue(marker_a.exists(), "first allowed tool must execute")
        self.assertTrue(marker_b.exists(), "second tool must execute via session allow")
        # ...tapi picker hanya muncul SEKALI.
        self.assertEqual(counter["asks"], 1, "second call must not re-ask")
        self.assertEqual(counter["options"], ("Allow once", "Allow sesi ini", "Deny"))
        self.assertTrue(
            self.approvals.session_allowed(identity, "run_shell"),
            "session allow must be cached",
        )

    def test_new_identity_asks_again(self):
        # Identity berbeda = sesi berbeda = picker muncul lagi (tidak bocor).
        for identity, marker in (
            ("test:risk-sess-one", "SESS_ONE"),
            ("test:risk-sess-two", "SESS_TWO"),
        ):
            agent = self.agent_module.Zeline(
                identity=identity,
                tool_profile="full",
                workspace=str(self.workspace),
            )
            counter = {"asks": 0}
            self._answerer(identity, "Allow sesi ini", counter)
            with mock.patch.object(
                self.agent_module.requests,
                "post",
                side_effect=[
                    FakeProviderResponse(self._shell_call_payload(marker)),
                    FakeProviderResponse(self._final_payload()),
                ],
            ):
                agent.send("jalankan perintah")
            self.assertEqual(counter["asks"], 1, f"{identity} must be asked")
            self.assertTrue((self.workspace / marker).exists())

    def test_allow_once_still_asks_every_time(self):
        # "Allow once" tidak menyentuh cache: panggilan kedua tetap bertanya.
        identity = "test:risk-allow-once"
        agent = self.agent_module.Zeline(
            identity=identity, tool_profile="full", workspace=str(self.workspace)
        )
        counter = {"asks": 0}
        self._answerer(identity, "Allow once", counter)
        with mock.patch.object(
            self.agent_module.requests,
            "post",
            side_effect=[
                FakeProviderResponse(self._shell_call_payload("ONCE_A")),
                FakeProviderResponse(self._final_payload()),
                FakeProviderResponse(self._shell_call_payload("ONCE_B")),
                FakeProviderResponse(self._final_payload()),
            ],
        ):
            agent.send("perintah pertama")
            agent.send("perintah kedua")
        self.assertEqual(counter["asks"], 2, "allow-once must ask every time")
        self.assertTrue((self.workspace / "ONCE_A").exists())
        self.assertTrue((self.workspace / "ONCE_B").exists())
        self.assertFalse(self.approvals.session_allowed(identity, "run_shell"))


class SessionLifecycleClearingTests(ApprovalsBase):
    def test_reset_clears_session_allows(self):
        identity = "test:reset-clear"
        store = self.sessions.SessionStore()
        self.approvals.grant_session_allow(identity, "run_shell")
        store.reset(identity)
        self.assertFalse(self.approvals.session_allowed(identity, "run_shell"))

    def test_eviction_clears_session_allows(self):
        # Sesi yang di-evict karena max_sessions = sesi yang sudah berakhir:
        # izinnya tidak boleh bertahan.
        store = self.sessions.SessionStore(max_sessions=1)
        store.get_or_create("test:evict-a", tool_profile="full", workspace=str(self.workspace))
        self.approvals.grant_session_allow("test:evict-a", "run_shell")
        store.get_or_create("test:evict-b", tool_profile="full", workspace=str(self.workspace))
        self.assertFalse(self.approvals.session_allowed("test:evict-a", "run_shell"))

    def test_thread_safety_smoke(self):
        # Grant/check/clear konkuren tidak boleh corrupt (best-effort smoke).
        errors = []

        def worker(n):
            try:
                identity = f"test:race-{n % 4}"
                for _ in range(50):
                    self.approvals.grant_session_allow(identity, "run_shell")
                    self.approvals.session_allowed(identity, "run_shell")
                    self.approvals.clear_session_allows(identity)
            except Exception as exc:  # noqa: BLE001 — dicatat, bukan ditelan
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
