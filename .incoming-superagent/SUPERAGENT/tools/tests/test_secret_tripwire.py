import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import secret_tripwire as S  # noqa: E402


class SecretTripwireTest(unittest.TestCase):
    def test_detects_evm_private_key(self):
        text = "key: 0x" + "a" * 64
        kinds = {f.kind for f in S.scan(text)}
        self.assertIn("evm_private_key", kinds)

    def test_detects_openai_key(self):
        kinds = {f.kind for f in S.scan("token sk-abcdefghij1234567890XYZ")}
        self.assertIn("openai_key", kinds)

    def test_redacts(self):
        red = S.redact("here 0x" + "b" * 64 + " done")
        self.assertNotIn("b" * 64, red)
        self.assertIn("REDACTED", red)

    def test_redact_idempotent_clean_text(self):
        clean = "ini teks biasa tanpa secret apa pun, cuma 1234 dan kata."
        self.assertEqual(S.redact(clean), clean)

    def test_guard_strict_raises(self):
        with self.assertRaises(S.SecretLeakError):
            S.guard("aws AKIA1234567890ABCDEF here", strict=True)

    def test_guard_nonstrict_redacts(self):
        out = S.guard("aws AKIA1234567890ABCDEF here", strict=False)
        self.assertNotIn("AKIA1234567890ABCD", out)

    def test_mnemonic_needs_hint(self):
        # 12 kata biasa TANPA hint → jangan flag (false positive control)
        plain = "the quick brown fox jumps over the lazy dog near old barn"
        self.assertEqual([f.kind for f in S.scan(plain) if f.kind == "mnemonic"], [])
        # dengan hint → flag
        withh = "mnemonic: ridge layer broom apple ocean canyon table velvet maple river stone arrow"
        self.assertIn("mnemonic", {f.kind for f in S.scan(withh)})

    def test_jwt_detected(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123_def456"
        self.assertIn("jwt", {f.kind for f in S.scan(jwt)})


if __name__ == "__main__":
    unittest.main()
