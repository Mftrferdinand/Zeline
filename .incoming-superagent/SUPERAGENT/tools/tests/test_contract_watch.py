import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import contract_watch as C  # noqa: E402


class ContractWatchTest(unittest.TestCase):
    def test_no_change(self):
        snap = C.ContractSnapshot("0x1", impl_address="0xa", claim_address="0xc",
                                  admin="0xad", code_hash="h", functions=["claim"])
        r = C.diff(snap, snap)
        self.assertFalse(r.changed)
        self.assertEqual(r.max_severity, "info")

    def test_claim_address_change_critical(self):
        p = C.ContractSnapshot("0x1", claim_address="0xc1")
        c = C.ContractSnapshot("0x1", claim_address="0xc2")
        r = C.diff(p, c)
        self.assertEqual(r.max_severity, "critical")
        self.assertTrue(any(a.kind == "claim_address_changed" for a in r.alerts))

    def test_proxy_upgrade_critical(self):
        p = C.ContractSnapshot("0x1", impl_address="0xa")
        c = C.ContractSnapshot("0x1", impl_address="0xb")
        self.assertEqual(C.diff(p, c).max_severity, "critical")

    def test_admin_change_warning(self):
        p = C.ContractSnapshot("0x1", admin="0xa")
        c = C.ContractSnapshot("0x1", admin="0xb")
        r = C.diff(p, c)
        self.assertEqual(r.max_severity, "warning")

    def test_sensitive_function_added(self):
        p = C.ContractSnapshot("0x1", functions=["claim"])
        c = C.ContractSnapshot("0x1", functions=["claim", "setClaimAddress"])
        r = C.diff(p, c)
        self.assertTrue(any(a.kind == "sensitive_function_added" for a in r.alerts))
        self.assertEqual(r.max_severity, "critical")

    def test_benign_function_info_only(self):
        p = C.ContractSnapshot("0x1", functions=["claim"])
        c = C.ContractSnapshot("0x1", functions=["claim", "totalSupply"])
        r = C.diff(p, c)
        self.assertEqual(r.max_severity, "info")

    def test_safe_to_claim(self):
        p = C.ContractSnapshot("0x1", claim_address="0xc1")
        same = C.ContractSnapshot("0x1", claim_address="0xc1")
        evil = C.ContractSnapshot("0x1", claim_address="0xc2")
        self.assertTrue(C.safe_to_claim(p, same))
        self.assertFalse(C.safe_to_claim(p, evil))


if __name__ == "__main__":
    unittest.main()
