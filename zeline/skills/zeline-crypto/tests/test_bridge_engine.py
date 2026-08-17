"""Tests for bridge_engine.py: address -> bytes32 padding helper."""
import _bootstrap  # noqa: F401

import unittest

import bridge_engine as B


class TestAddrToBytes32(unittest.TestCase):
    def test_pads_left_to_32_bytes(self):
        addr = "0x" + "ab" * 20
        out = B._addr_to_bytes32(addr)
        self.assertEqual(len(out), 32)
        self.assertEqual(out[:12], bytes(12))
        self.assertEqual(out[12:], bytes.fromhex("ab" * 20))

    def test_works_without_0x_prefix(self):
        addr = "cd" * 20
        out = B._addr_to_bytes32(addr)
        self.assertEqual(len(out), 32)
        self.assertEqual(out[12:], bytes.fromhex("cd" * 20))


if __name__ == "__main__":
    unittest.main()
