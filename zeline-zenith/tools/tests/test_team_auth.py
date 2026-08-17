#!/usr/bin/env python3
"""
test_team_auth.py — Unit tests for cryptographic team authentication.

Covers the security-critical paths of tools/team_auth.py:
  - Zero-dependency secp256k1 signature recovery (Strategy 5) correctness
  - Full challenge-response verification (happy path)
  - Anti-replay: one-time challenge consumption
  - Challenge expiry (TTL) rejection
  - Level gating (Sovereign vs Observer)
  - Treasury op authorization (fail-closed on wrong level / bad sig)
  - can_verify is ALWAYS True (no silent no-op auth)

Runs fully offline with NO external crypto libraries required — proving the
pure-stdlib fallback keeps auth functional on a vanilla host.
"""
import importlib.util
import secrets
import time
import unittest
from pathlib import Path

# Load team_auth as a module regardless of package context
_HERE = Path(__file__).resolve().parent
_TA_PATH = _HERE.parent / "team_auth.py"
_spec = importlib.util.spec_from_file_location("team_auth_under_test", _TA_PATH)
ta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ta)

N = ta._SECP256K1_N
G = (ta._SECP256K1_GX, ta._SECP256K1_GY)


def _gen_keypair():
    priv = secrets.randbelow(N - 1) + 1
    Q = ta._ec_mul(priv, G)
    pub = Q[0].to_bytes(32, "big") + Q[1].to_bytes(32, "big")
    addr = "0x" + ta._keccak256(pub)[-20:].hex()
    return priv, addr


def _personal_sign(msg: str, priv: int) -> str:
    prefix = "\x19Ethereum Signed Message:\n" + str(len(msg))
    z = int.from_bytes(ta._keccak256((prefix + msg).encode()), "big")
    while True:
        k = secrets.randbelow(N - 1) + 1
        R = ta._ec_mul(k, G)
        r = R[0] % N
        if r == 0:
            continue
        s = (ta._inv_mod(k, N) * (z + r * priv)) % N
        if s == 0:
            continue
        rec_id = R[1] % 2
        if s > N // 2:  # enforce low-s (EIP-2)
            s = N - s
            rec_id = rec_id ^ 1
        return "0x" + r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex() + bytes([27 + rec_id]).hex()


class ZeroDepRecoveryTest(unittest.TestCase):
    def test_sign_recover_roundtrip(self):
        """Zero-dep recovery must return the exact signer address."""
        for _ in range(10):
            priv, addr = _gen_keypair()
            sig = _personal_sign("Hello World", priv)
            recovered = ta._recover_address_zerodep("Hello World", sig)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.lower(), addr.lower())

    def test_wrong_message_fails(self):
        priv, addr = _gen_keypair()
        sig = _personal_sign("original message", priv)
        recovered = ta._recover_address_zerodep("tampered message", sig)
        # Recovery yields SOME address, but never the real signer's
        self.assertNotEqual((recovered or "").lower(), addr.lower())

    def test_malformed_signature(self):
        self.assertIsNone(ta._recover_address_zerodep("x", "0xdeadbeef"))
        self.assertIsNone(ta._recover_address_zerodep("x", "not-hex"))


class TeamAuthFlowTest(unittest.TestCase):
    def setUp(self):
        # Isolated config per test (temp file), no external deps
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.auth = ta.TeamAuth(config_path=Path(self.tmp.name))
        self.priv, self.addr = _gen_keypair()

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_can_verify_always_true(self):
        """Auth must never silently degrade to a no-op, even with zero libs."""
        self.assertTrue(self.auth._can_verify)

    def test_challenge_response_happy_path(self):
        self.auth.register_address("op_001", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        challenge = self.auth.generate_challenge()
        sig = _personal_sign(challenge, self.priv)
        result = self.auth.verify_challenge(challenge, sig, self.addr)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.level, ta.LEVEL_SOVEREIGN)
        self.assertEqual(result.member_name, "Boss")

    def test_replay_rejected(self):
        """A consumed challenge cannot be reused."""
        self.auth.register_address("op_001", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        challenge = self.auth.generate_challenge()
        sig = _personal_sign(challenge, self.priv)
        first = self.auth.verify_challenge(challenge, sig, self.addr)
        self.assertTrue(first.authenticated)
        second = self.auth.verify_challenge(challenge, sig, self.addr)
        self.assertFalse(second.authenticated)

    def test_expired_challenge_rejected(self):
        self.auth.register_address("op_001", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        challenge = self.auth.generate_challenge()
        # Force expiry
        for c in self.auth._active_challenges.values():
            c.created_at -= (ta.CHALLENGE_TTL_SECONDS + 1)
        sig = _personal_sign(challenge, self.priv)
        result = self.auth.verify_challenge(challenge, sig, self.addr)
        self.assertFalse(result.authenticated)
        self.assertIn("expired", result.error.lower())

    def test_bad_signature_rejected(self):
        self.auth.register_address("op_001", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        challenge = self.auth.generate_challenge()
        other_priv, _ = _gen_keypair()
        bad_sig = _personal_sign(challenge, other_priv)  # signed by wrong key
        result = self.auth.verify_challenge(challenge, bad_sig, self.addr)
        self.assertFalse(result.authenticated)

    def test_unregistered_address_gets_observer(self):
        challenge = self.auth.generate_challenge()
        sig = _personal_sign(challenge, self.priv)
        result = self.auth.verify_challenge(challenge, sig, self.addr)
        # Crypto valid but not in team → observer, flagged
        self.assertTrue(result.authenticated)
        self.assertEqual(result.level, ta.LEVEL_OBSERVER)

    def test_level_lookup(self):
        self.auth.register_address("op_002", self.addr, ta.LEVEL_OPERATOR, "Dev")
        self.assertEqual(self.auth.get_level(self.addr), ta.LEVEL_OPERATOR)
        self.assertEqual(self.auth.get_level("0x" + "00" * 20), ta.LEVEL_OBSERVER)


class TreasuryAuthTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.auth = ta.TeamAuth(config_path=Path(self.tmp.name))
        self.priv, self.addr = _gen_keypair()

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_treasury_op_authorized_for_sovereign(self):
        self.auth.register_address("boss", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        challenge = self.auth.generate_treasury_challenge("withdraw", 5000.0)
        sig = _personal_sign(challenge, self.priv)
        ok = self.auth.authorize_treasury_op(self.addr, sig, 5000.0, "withdraw")
        self.assertTrue(ok)

    def test_treasury_op_rejected_for_observer(self):
        """Fail-closed: non-Sovereign cannot move funds even with valid sig."""
        self.auth.register_address("obs", self.addr, ta.LEVEL_OBSERVER, "Client")
        challenge = self.auth.generate_treasury_challenge("withdraw", 5000.0)
        sig = _personal_sign(challenge, self.priv)
        ok = self.auth.authorize_treasury_op(self.addr, sig, 5000.0, "withdraw")
        self.assertFalse(ok)

    def test_treasury_op_rejected_unknown_operation(self):
        self.auth.register_address("boss", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        challenge = self.auth.generate_treasury_challenge("withdraw", 100.0)
        sig = _personal_sign(challenge, self.priv)
        ok = self.auth.authorize_treasury_op(self.addr, sig, 100.0, "definitely_not_a_real_op")
        self.assertFalse(ok)

    def test_treasury_op_rejected_without_challenge(self):
        self.auth.register_address("boss", self.addr, ta.LEVEL_SOVEREIGN, "Boss")
        ok = self.auth.authorize_treasury_op(self.addr, "0x" + "00" * 65, 100.0, "withdraw")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
