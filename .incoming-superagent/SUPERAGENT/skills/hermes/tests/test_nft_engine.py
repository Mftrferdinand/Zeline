"""Tests for nft_engine.py: NFTResult default safety + Reservoir chain map."""
import _bootstrap  # noqa: F401

import unittest

import nft_engine as N


class TestNFTResultDefault(unittest.TestCase):
    def test_tx_hashes_defaults_to_independent_list(self):
        a = N.NFTResult(status="ok")
        b = N.NFTResult(status="ok")
        self.assertEqual(a.tx_hashes, [])
        a.tx_hashes.append("0x1")
        self.assertEqual(b.tx_hashes, [])


class TestReservoirChains(unittest.TestCase):
    def test_known_chains_present(self):
        self.assertEqual(N.RESERVOIR_CHAINS[1], "ethereum")
        self.assertEqual(N.RESERVOIR_CHAINS[8453], "base")
        self.assertIn(42161, N.RESERVOIR_CHAINS)


if __name__ == "__main__":
    unittest.main()
