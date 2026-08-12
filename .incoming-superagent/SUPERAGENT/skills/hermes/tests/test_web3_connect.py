"""Tests for web3_connect.py pure helpers (nonce, signature split, SIWE render)."""
import _bootstrap  # noqa: F401

import string
import unittest

import web3_connect as W


class TestGenerateNonce(unittest.TestCase):
    def test_length_and_alphabet(self):
        n = W.generate_nonce()
        self.assertEqual(len(n), 16)
        allowed = set(string.ascii_letters + string.digits)
        self.assertTrue(set(n) <= allowed)

    def test_custom_length_and_uniqueness(self):
        self.assertEqual(len(W.generate_nonce(32)), 32)
        self.assertNotEqual(W.generate_nonce(24), W.generate_nonce(24))


class TestSplitSignature(unittest.TestCase):
    def test_splits_65_bytes(self):
        r = "11" * 32
        s = "22" * 32
        v = "1b"  # 27
        sig = "0x" + r + s + v
        got_v, got_r, got_s = W.split_signature(sig)
        self.assertEqual(got_v, 27)
        self.assertEqual(got_r, bytes.fromhex(r))
        self.assertEqual(got_s, bytes.fromhex(s))

    def test_accepts_without_0x(self):
        sig = ("11" * 32) + ("22" * 32) + "1c"
        v, _, _ = W.split_signature(sig)
        self.assertEqual(v, 28)

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            W.split_signature("0x1234")


class TestSiweMessage(unittest.TestCase):
    def test_render_contains_core_fields(self):
        msg = W.SiweMessage(
            domain="app.example", address="0xABC", statement="Sign in to Example",
            uri="https://app.example", chain_id=1, nonce="abc123",
            issued_at="2025-01-01T00:00:00Z",
        )
        out = msg.render()
        self.assertIn("app.example wants you to sign in", out)
        self.assertIn("0xABC", out)
        self.assertIn("Chain ID: 1", out)
        self.assertIn("Nonce: abc123", out)
        self.assertIn("Issued At: 2025-01-01T00:00:00Z", out)
        self.assertNotIn("Expiration Time", out)

    def test_render_includes_expiration_when_set(self):
        msg = W.SiweMessage(
            domain="d", address="0x1", statement="s", uri="u", chain_id=8453,
            nonce="n", issued_at="2025-01-01T00:00:00Z",
            expiration_time="2025-01-02T00:00:00Z",
        )
        self.assertIn("Expiration Time: 2025-01-02T00:00:00Z", msg.render())

    def test_render_autofills_issued_at(self):
        msg = W.SiweMessage(domain="d", address="0x1", statement="s", uri="u",
                            chain_id=1, nonce="n")
        self.assertIn("Issued At: ", msg.render())
        self.assertIsNotNone(msg.issued_at)


if __name__ == "__main__":
    unittest.main()
