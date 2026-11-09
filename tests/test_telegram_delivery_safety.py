"""An ambiguous Telegram response cannot authorize replay or fake delivery."""
import importlib
from pathlib import Path
import tempfile
import unittest


class FinalReceiptSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zeline-receipt-")
        self.addCleanup(self.temporary.cleanup)
        self.receipts = importlib.import_module("zeline.telegram_delivery")

    def test_upstream_5xx_is_unknown_and_never_automatically_replayed(self):
        receipt = self.receipts.FinalReceipt(Path(self.temporary.name), 1, ["offline final"])
        calls = []
        def send(index, text):
            calls.append((index, text))
            return {"ok": False, "error_code": 503, "parameters": {"retry_after": 0}}
        self.assertIs(receipt.deliver(send, lambda: False), False)
        self.assertEqual(len(calls), 1, "503 does not prove Telegram failed to accept the message")
        self.assertEqual(receipt.record["parts"][0]["state"], "UNKNOWN")

    def test_acknowledged_part_is_not_replayed_after_a_later_write_failure(self):
        receipt = self.receipts.FinalReceipt(Path(self.temporary.name), 1, ["offline final"])
        original = receipt.save
        def save():
            if receipt.record["parts"][0]["state"] == "ACKED":
                raise OSError("offline fsync failure after Telegram ACK")
            original()
        receipt.save = save
        calls = []
        def send(index, text):
            calls.append((index, text))
            return {"ok": True, "result": {"message_id": 300}}
        with self.assertRaises(OSError):
            receipt.deliver(send, lambda: False)
        self.assertEqual(calls, [(0, "offline final")])
        self.assertEqual(receipt.record["parts"][0]["state"], "ACKED")
        self.assertIs(receipt.deliver(send, lambda: False), True)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
