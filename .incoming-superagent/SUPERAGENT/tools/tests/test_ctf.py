import base64
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import ctf as CTF  # noqa: E402


class CtfTest(unittest.TestCase):
    def test_find_flags(self):
        self.assertEqual(CTF.find_flags("yay flag{abc_123} here"), ["flag{abc_123}"])
        self.assertEqual(CTF.find_flags("CTF{multi} and HTB{two}"),
                         ["CTF{multi}", "HTB{two}"])

    def test_triage_crypto(self):
        cats = dict(CTF.triage("RSA modulus nonce AES encrypt"))
        self.assertEqual(max(cats, key=cats.get), "crypto")

    def test_decode_base64(self):
        enc = base64.b64encode(b"flag{decoded}").decode()
        attempts = CTF.try_decode(enc)
        self.assertTrue(any("flag{decoded}" in a.value for a in attempts))

    def test_decode_hex(self):
        h = b"hello".hex()
        attempts = CTF.try_decode(h)
        self.assertTrue(any(a.method == "hex" and a.value == "hello" for a in attempts))

    def test_caesar_bruteforce_recovers(self):
        # "ABC" shift 3 → "DEF"; brute harus punya entri yang balik ke ABC
        shifted = "DEF"
        results = dict(CTF.caesar_bruteforce(shifted))
        self.assertEqual(results[3], "ABC")

    def test_xor_single_byte_recovers(self):
        plain = b"this is a secret message about flags"
        key = 0x42
        ct = bytes(b ^ key for b in plain)
        top = CTF.xor_single_byte(ct)
        self.assertTrue(any(k == key for k, _, _ in top))

    def test_xor_repeating_roundtrip(self):
        data = b"attack at dawn"
        key = b"KEY"
        enc = CTF.xor_repeating(data, key)
        self.assertEqual(CTF.xor_repeating(enc, key), data)

    def test_identify_hash(self):
        self.assertIn("MD5", CTF.identify_hash("5f4dcc3b5aa765d61d8327deb882cf99"))
        self.assertIn("SHA-256", CTF.identify_hash("a" * 64))
        self.assertEqual(CTF.identify_hash("$2b$12$abc"), ["bcrypt"])


if __name__ == "__main__":
    unittest.main()
