"""Tests for swap_engine.py: explorer URL builder + SwapResult default safety."""
import _bootstrap  # noqa: F401

import unittest

import swap_engine as S


class TestExplorerUrl(unittest.TestCase):
    def test_known_chain(self):
        url = S._explorer_url(1, "0xdeadbeef")
        self.assertTrue(url.endswith("0xdeadbeef"))
        self.assertTrue(url.startswith("http"))

    def test_unknown_chain_returns_bare_hash(self):
        # Unknown chain id -> empty prefix, so just the hash remains.
        self.assertEqual(S._explorer_url(999999, "0xabc"), "0xabc")


class TestSwapResultDefault(unittest.TestCase):
    def test_warnings_defaults_to_independent_list(self):
        a = S.SwapResult(status="sent")
        b = S.SwapResult(status="sent")
        self.assertEqual(a.warnings, [])
        a.warnings.append("x")
        # The mutable-default bug would make b.warnings also contain 'x'.
        self.assertEqual(b.warnings, [])


class TestNativeSentinel(unittest.TestCase):
    def test_sentinel_is_checksum_eeee(self):
        self.assertEqual(S.NATIVE_SENTINEL_EVM.lower(),
                         "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")


if __name__ == "__main__":
    unittest.main()
