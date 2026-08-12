import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import sybil_audit as SA  # noqa: E402


def _correlated():
    return [
        SA.WalletActivity("0x1", funded_by="0xCEX", first_tx_ts=1000, tx_count=10,
                          gas_price_gwei=20, interacted_contracts=("0xa", "0xb")),
        SA.WalletActivity("0x2", funded_by="0xCEX", first_tx_ts=1100, tx_count=10,
                          gas_price_gwei=20, interacted_contracts=("0xa", "0xb")),
        SA.WalletActivity("0x3", funded_by="0xCEX", first_tx_ts=1200, tx_count=10,
                          gas_price_gwei=20, interacted_contracts=("0xa", "0xb")),
    ]


def _dispersed():
    return [
        SA.WalletActivity("0x1", funded_by="0xA", first_tx_ts=1000, tx_count=10,
                          gas_price_gwei=18, interacted_contracts=("0xa", "0xc")),
        SA.WalletActivity("0x2", funded_by="0xB", first_tx_ts=900000, tx_count=37,
                          gas_price_gwei=45, interacted_contracts=("0xd", "0xe")),
        SA.WalletActivity("0x3", funded_by="0xC", first_tx_ts=5000000, tx_count=72,
                          gas_price_gwei=12, interacted_contracts=("0xf",)),
    ]


class SybilAuditTest(unittest.TestCase):
    def test_high_risk_on_correlated(self):
        r = SA.audit(_correlated())
        self.assertEqual(r.risk, "high")
        self.assertGreaterEqual(r.score, 60)
        self.assertTrue(r.advice)

    def test_low_risk_on_dispersed(self):
        r = SA.audit(_dispersed())
        self.assertIn(r.risk, ("low", "medium"))
        self.assertLess(r.score, 60)

    def test_single_wallet_low(self):
        r = SA.audit([SA.WalletActivity("0x1")])
        self.assertEqual(r.risk, "low")
        self.assertEqual(r.score, 0)

    def test_shared_funding_signal_present(self):
        r = SA.audit(_correlated())
        self.assertTrue(any("funding" in s for s in r.signals))


if __name__ == "__main__":
    unittest.main()
