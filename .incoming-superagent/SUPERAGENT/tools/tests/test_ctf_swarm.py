"""Offline tests for the CTF swarm framework's deterministic helpers.

Covers the pure-logic, zero-network pieces that gate correctness/safety:
  * scope_guard  — authorization allowlist (the whitehat line)
  * flag_validator — anti-hallucination flag acceptance
  * rsa_attacks  — offline RSA math (factordb/network path is NOT tested here)

The online runtime (coordinator/solver/llm/sandbox/ctfd_client) needs
requests + Docker + API keys, so it is import-checked but not exercised here.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CTF_DIR = ROOT / "ctf"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scope_guard = _load("sg_test", CTF_DIR / "scope_guard.py")
flag_validator = _load("fv_test", CTF_DIR / "flag_validator.py")
rsa_attacks = _load("rsa_test", CTF_DIR / "rsa_attacks.py")
gandalf = _load("gandalf_test", CTF_DIR / "gandalf_solver.py")


class ScopeGuardTest(unittest.TestCase):
    SCOPE = {
        "competition": "Test CTF",
        "allow_hosts": ["chal.ctf.example", "*.ctf.example"],
        "allow_cidrs": ["10.13.37.0/24"],
        "deny_hosts": ["admin.ctf.example"],
    }

    def test_exact_host_allowed(self):
        ok, _ = scope_guard.is_in_scope("https://chal.ctf.example/01", self.SCOPE)
        self.assertTrue(ok)

    def test_wildcard_allowed(self):
        ok, _ = scope_guard.is_in_scope("web01.ctf.example", self.SCOPE)
        self.assertTrue(ok)

    def test_deny_wins_over_allow(self):
        # admin.ctf.example matches the *.ctf.example allow rule but deny wins
        ok, reason = scope_guard.is_in_scope("admin.ctf.example", self.SCOPE)
        self.assertFalse(ok)
        self.assertIn("deny", reason)

    def test_offscope_refused(self):
        ok, _ = scope_guard.is_in_scope("evil.com", self.SCOPE)
        self.assertFalse(ok)

    def test_ip_literal_in_cidr(self):
        ok, _ = scope_guard.is_in_scope("10.13.37.5:1337", self.SCOPE)
        self.assertTrue(ok)

    def test_ip_literal_out_of_cidr(self):
        ok, _ = scope_guard.is_in_scope("10.13.38.5", self.SCOPE)
        self.assertFalse(ok)

    def test_host_port_parsing(self):
        self.assertEqual(scope_guard._host_from_target("chal.ctf.example:9999"),
                         "chal.ctf.example")
        self.assertEqual(scope_guard._host_from_target("nc://x.ctf.example/path"),
                         "x.ctf.example")

    def test_assert_raises_and_passes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(self.SCOPE, fh)
            path = fh.name
        # in-scope: no raise
        scope_guard.assert_in_scope("chal.ctf.example", path)
        # out-of-scope: raises ScopeError
        with self.assertRaises(scope_guard.ScopeError):
            scope_guard.assert_in_scope("evil.com", path)


class FlagValidatorTest(unittest.TestCase):
    def test_valid_with_explicit_format(self):
        self.assertTrue(flag_validator.validate("flag{abc_123}", r"flag\{.*\}"))

    def test_valid_with_common_format(self):
        self.assertTrue(flag_validator.validate("picoCTF{y3s}"))
        self.assertTrue(flag_validator.validate("HTB{abc}"))

    def test_placeholder_rejected(self):
        for ph in ("flag{...}", "flag{your_flag_here}", "FLAG{EXAMPLE}", "flag{}"):
            self.assertFalse(flag_validator.validate(ph), ph)

    def test_empty_rejected(self):
        self.assertFalse(flag_validator.validate(""))

    def test_junk_wrapped_rejected_by_validate(self):
        # validate is strict (fullmatch) — wrapped text must NOT validate
        self.assertFalse(flag_validator.validate("see flag{x} here", r"flag\{.*\}"))

    def test_extract_all_scrapes_blob(self):
        blob = "noise flag{one} more CTF{two} flag{one} end"
        got = flag_validator.extract_all(blob)
        self.assertIn("flag{one}", got)
        self.assertIn("CTF{two}", got)
        # dedupe preserves order, first occurrence only
        self.assertEqual(got.count("flag{one}"), 1)

    def test_extract_drops_placeholders(self):
        self.assertEqual(flag_validator.extract_all("flag{...}"), [])

    def test_extract_with_explicit_format(self):
        got = flag_validator.extract_all("x=flag{deadbeef}", r"flag\{[0-9a-f]+\}")
        self.assertEqual(got, ["flag{deadbeef}"])


class RsaAttacksTest(unittest.TestCase):
    def test_small_e_cube_root(self):
        m = 42
        c = m ** 3
        self.assertEqual(rsa_attacks.small_e_root(c, 3), m)

    def test_small_e_not_exact(self):
        self.assertIsNone(rsa_attacks.small_e_root(28, 3))  # not a perfect cube

    def test_fermat_close_primes(self):
        p, q = 1000003, 1000033  # close primes
        n = p * q
        f = rsa_attacks.fermat_factor(n)
        self.assertIsNotNone(f)
        self.assertEqual(sorted(f), [p, q])

    def test_decrypt_with_factors_roundtrip(self):
        p, q, e = 61, 53, 17
        n = p * q
        m = 65
        c = pow(m, e, n)
        self.assertEqual(rsa_attacks.decrypt_with_factors(c, e, p, q), m)

    def test_wiener_small_d(self):
        # construct an RSA key with a small private exponent d (Wiener-vulnerable)
        from math import gcd
        p, q = 999983, 999979
        n = p * q
        phi = (p - 1) * (q - 1)
        d = next(x for x in range(3, 100, 2) if gcd(x, phi) == 1)  # tiny coprime d
        e = pow(d, -1, phi)
        self.assertEqual(rsa_attacks.wiener(e, n), d)

    def test_common_modulus(self):
        p, q = 61, 53
        n = p * q
        m = 42
        e1, e2 = 17, 23
        from math import gcd
        self.assertEqual(gcd(e1, e2), 1)
        c1, c2 = pow(m, e1, n), pow(m, e2, n)
        self.assertEqual(rsa_attacks.common_modulus(c1, c2, e1, e2, n), m)

    def test_long_to_bytes(self):
        self.assertEqual(rsa_attacks.long_to_bytes(int.from_bytes(b"hi", "big")), b"hi")


class GandalfSolverTest(unittest.TestCase):
    """Offline tests for the prompt-injection solver's pure extraction + host lock."""

    def test_allcaps_token_extracted(self):
        c = gandalf.candidates("The secret is COCOLOCO, please keep it safe.")
        self.assertIn("COCOLOCO", c)

    def test_noise_words_filtered(self):
        # PASSWORD/SECRET/SORRY etc. must not be offered as guesses
        c = gandalf.candidates("SORRY I CANNOT REVEAL THE PASSWORD SECRET")
        for junk in ("PASSWORD", "SECRET", "SORRY", "CANNOT", "REVEAL"):
            self.assertNotIn(junk, c)

    def test_acrostic_reconstruction(self):
        ans = "Wonderful\nAmazing\nVivid\nElegant\nSunny"  # -> WAVES
        self.assertIn("WAVES", gandalf.candidates(ans))

    def test_separated_letters_joined(self):
        c = gandalf.candidates("here it is: C # O # C # O # A done")
        self.assertIn("COCOA", c)

    def test_base64_token_decoded(self):
        import base64
        tok = base64.b64encode(b"PASSCODE").decode()
        self.assertIn("PASSCODE", gandalf.candidates(f"encoded: {tok}"))

    def test_empty_answer_no_candidates(self):
        self.assertEqual(gandalf.candidates(""), [])

    def test_candidates_dedupe_order(self):
        c = gandalf.candidates("FLAGWORD FLAGWORD other text FLAGWORD")
        self.assertEqual(c.count("FLAGWORD"), 1)

    def test_host_lock_rejects_other_host(self):
        with self.assertRaises(SystemExit):
            gandalf._check_host("https://evil.example/api/send-message")

    def test_host_lock_allows_gandalf(self):
        # should not raise
        gandalf._check_host("https://gandalf.lakera.ai/api/send-message")

    def test_defenders_cover_eight_levels(self):
        self.assertEqual(set(gandalf.DEFENDERS), set(range(1, 9)))


class LlmDispatchTest(unittest.TestCase):
    """Category-aware AI/LLM (sk48) wiring in the coordinator/solver."""

    def setUp(self):
        sys.path.insert(0, str(CTF_DIR))
        from coordinator import solver, coordinator as coord
        from coordinator.ctfd_client import Challenge
        self.solver, self.coord, self.Challenge = solver, coord, Challenge

    def tearDown(self):
        if str(CTF_DIR) in sys.path:
            sys.path.remove(str(CTF_DIR))

    def test_llm_categories_detected(self):
        for cat in ("Prompt Injection", "AI", "llm", "GPT-jail", "Gandalf",
                    "LLM red-team"):
            self.assertTrue(self.solver._is_llm_category(cat), cat)

    def test_non_llm_categories_rejected(self):
        # "explain"/"chain"/"blockchain" must not trip the "ai" word match
        for cat in ("web", "pwn", "crypto", "rev", "forensics", "blockchain",
                    "explain-chain", "misc"):
            self.assertFalse(self.solver._is_llm_category(cat), cat)

    def test_build_system_adds_addendum_only_for_llm(self):
        self.assertIn(self.solver.LLM_ADDENDUM, self.solver.build_system("AI"))
        self.assertNotIn(self.solver.LLM_ADDENDUM, self.solver.build_system("web"))
        # base methodology always present
        self.assertIn(self.solver.SYSTEM, self.solver.build_system("web"))

    def test_extract_targets_from_description_and_conn(self):
        c = self.Challenge(id=1, name="g", category="AI", value=100, solved=False,
                           description="pwn https://gandalf.lakera.ai/ and http://a.ctf/x",
                           connection_info="", files=[])
        t = self.coord.extract_targets(c)
        self.assertIn("https://gandalf.lakera.ai/", t)
        self.assertIn("http://a.ctf/x", t)

    def test_extract_targets_conn_first(self):
        c = self.Challenge(id=2, name="p", category="pwn", value=1, solved=False,
                           description="just pwn", connection_info="nc h.ctf 1337",
                           files=[])
        self.assertEqual(self.coord.extract_targets(c), ["nc h.ctf 1337"])

    def test_extract_targets_offline_empty(self):
        c = self.Challenge(id=3, name="r", category="rev", value=1, solved=False,
                           description="reverse the binary", connection_info="",
                           files=[])
        self.assertEqual(self.coord.extract_targets(c), [])


class ProviderAdapterTest(unittest.TestCase):
    """Offline checks for the OpenAI/Gemini wire-format converters in llm.py.

    The canonical history is the Anthropic block shape; each provider adapter
    must convert it losslessly enough to replay the same tool-use loop.
    """

    TOOLS = [{"name": "run", "description": "run a command",
              "input_schema": {"type": "object",
                               "properties": {"command": {"type": "string"}},
                               "required": ["command"]}}]
    HISTORY = [
        {"role": "user", "content": "Challenge: cat flag"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking files."},
            {"type": "tool_use", "id": "tu_1", "name": "run",
             "input": {"command": "ls /work"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": "rc=0\nflag.txt"},
        ]},
    ]

    def setUp(self):
        sys.path.insert(0, str(CTF_DIR))
        from coordinator import llm
        self.llm = llm

    def tearDown(self):
        if str(CTF_DIR) in sys.path:
            sys.path.remove(str(CTF_DIR))

    def test_make_adapter_dispatch(self):
        self.assertIsInstance(self.llm.make_adapter("openai:gpt-5", "k"),
                              self.llm.OpenAIAdapter)
        self.assertIsInstance(self.llm.make_adapter("gemini:gemini-2.5-pro", "k"),
                              self.llm.GeminiAdapter)
        self.assertIsInstance(self.llm.make_adapter("anthropic:claude-sonnet-4-5", "k"),
                              self.llm.AnthropicAdapter)

    def test_to_openai_shapes(self):
        import json
        msgs, tools = self.llm.to_openai("SYS", self.HISTORY, self.TOOLS)
        self.assertEqual(msgs[0], {"role": "system", "content": "SYS"})
        self.assertEqual(msgs[1], {"role": "user", "content": "Challenge: cat flag"})
        asst = msgs[2]
        self.assertEqual(asst["role"], "assistant")
        self.assertEqual(asst["content"], "Checking files.")
        tc = asst["tool_calls"][0]
        self.assertEqual((tc["id"], tc["function"]["name"]), ("tu_1", "run"))
        self.assertEqual(json.loads(tc["function"]["arguments"]),
                         {"command": "ls /work"})
        tool_msg = msgs[3]
        self.assertEqual((tool_msg["role"], tool_msg["tool_call_id"]),
                         ("tool", "tu_1"))
        self.assertIn("flag.txt", tool_msg["content"])
        self.assertEqual(tools[0]["function"]["parameters"],
                         self.TOOLS[0]["input_schema"])

    def test_from_openai_tool_call_and_text(self):
        data = {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None,
            "tool_calls": [{"id": "call_9", "type": "function", "function": {
                "name": "run", "arguments": "{\"command\": \"id\"}"}}]}}]}
        turn = self.llm.from_openai(data)
        self.assertEqual(turn.stop_reason, "tool_use")
        self.assertEqual(turn.tool_calls[0].input, {"command": "id"})
        # plain text answer -> native finish reason, no tool calls
        turn2 = self.llm.from_openai({"choices": [{
            "finish_reason": "stop",
            "message": {"content": "FLAG: flag{x}"}}]})
        self.assertEqual((turn2.stop_reason, turn2.text), ("stop", "FLAG: flag{x}"))
        # malformed arguments must not crash the loop
        turn3 = self.llm.from_openai({"choices": [{"finish_reason": "tool_calls",
            "message": {"tool_calls": [{"id": "c", "function": {
                "name": "run", "arguments": "{not json"}}]}}]})
        self.assertEqual(turn3.tool_calls[0].input, {})

    def test_to_gemini_shapes(self):
        body = self.llm.to_gemini("SYS", self.HISTORY, self.TOOLS)
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "SYS")
        c = body["contents"]
        self.assertEqual((c[0]["role"], c[0]["parts"][0]["text"]),
                         ("user", "Challenge: cat flag"))
        self.assertEqual(c[1]["role"], "model")
        fc = c[1]["parts"][1]["functionCall"]
        self.assertEqual((fc["name"], fc["args"]), ("run", {"command": "ls /work"}))
        # functionResponse must be keyed by NAME resolved from tool_use_id
        fr = c[2]["parts"][0]["functionResponse"]
        self.assertEqual(fr["name"], "run")
        self.assertIn("flag.txt", fr["response"]["result"])
        self.assertEqual(body["tools"][0]["functionDeclarations"][0]["name"], "run")

    def test_from_gemini_function_call_and_text(self):
        data = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "Let me look."},
            {"functionCall": {"name": "run", "args": {"command": "ls"}}},
        ]}}]}
        turn = self.llm.from_gemini(data)
        self.assertEqual(turn.stop_reason, "tool_use")
        self.assertEqual(turn.tool_calls[0].name, "run")
        self.assertTrue(turn.tool_calls[0].id)  # synthesized id present
        turn2 = self.llm.from_gemini({"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": "FAILED"}]}}]})
        self.assertEqual((turn2.stop_reason, turn2.text), ("stop", "FAILED"))

    def test_config_requires_provider_keys(self):
        from coordinator.config import Config
        cfg = Config(ctfd_url="http://x", ctfd_token="t",
                     models=["openai:gpt-5", "gemini:gemini-2.5-pro"],
                     anthropic_api_key="", openai_api_key="", gemini_api_key="")
        errs = " ".join(cfg.validate())
        self.assertIn("OPENAI_API_KEY", errs)
        self.assertIn("GEMINI_API_KEY", errs)
        self.assertNotIn("ANTHROPIC_API_KEY", errs)


class RuntimeImportTest(unittest.TestCase):
    """Online runtime should at least import (path wiring intact)."""
    def test_coordinator_package_imports(self):
        sys.path.insert(0, str(CTF_DIR))
        try:
            import coordinator  # noqa: F401
            from coordinator.swarm import race  # noqa: F401
            from coordinator.solver import solve, SYSTEM, build_system  # noqa: F401
            from coordinator.coordinator import extract_targets  # noqa: F401
            from coordinator.llm import make_adapter  # noqa: F401
        finally:
            sys.path.remove(str(CTF_DIR))


if __name__ == "__main__":
    unittest.main()
