"""Isolated narration tests: no agent imports, operator state, or tool execution."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "zeline" / "narration.py"


def new_gate():
    assert MODULE_PATH.is_file(), "NarrationGate module has not been implemented"
    spec = importlib.util.spec_from_file_location("isolated_narration", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NarrationGate()


class NarrationGateTests(unittest.TestCase):
    def test_preserves_finding_and_whitespace_exactly(self):
        gate = new_gate()
        text = "  Password salah.\n  Perlu kredensial baru.  "
        self.assertEqual(gate.select(text), text)

    def test_suppresses_only_recognized_routine_announcements(self):
        for text in (
            "saya buka dulu webnya", "oke gua coba masukan pw",
            "Gua cek dulu.", "Saya klik tombolnya.", "Aku ketik passwordnya.",
            "I'll open the website.", "I will click the button.",
            "I will type the password.", "I will read the file.",
            "Oke.", "OK!", "  \n  ",
        ):
            with self.subTest(text=text):
                self.assertIsNone(new_gate().select(text))

    def test_suppresses_multiline_only_when_every_line_is_routine(self):
        self.assertIsNone(new_gate().select("Oke.\n\nSaya klik tombolnya.\nGua cek dulu."))

    def test_preserves_important_mixed_unknown_and_decorated_content(self):
        for text in (
            "Saya buka dulu webnya, karena sesi sebelumnya kedaluwarsa.",
            "Saya klik tombolnya. Password salah.",
            "Saya klik tombolnya.\nPassword salah.",
            "Saya klik tombolnya?", "Boleh saya klik tombolnya?",
            '"Saya klik tombolnya."', "`Saya klik tombolnya.`",
            "```\nSaya klik tombolnya.\n```", "Error: Saya klik tombolnya.",
            "Saya buka file untuk memeriksa risiko sebelum migrasi.",
            "I will open the website to verify the authentication flow.",
            "I will delete the database.", "I will click the button after approval.",
            "Saya cek OTP dan verifikasi akun.", "Password salah",
            "Saya akan beralih ke API karena browser diblokir.",
            "Je vais ouvrir le site.", "网页打不开", "🔍 Saya klik tombolnya.",
            "🔍 Password salah.", "OK — verification requires your input.",
            "Saya klik tombolnya; saldo 100 menjadi 200.",
            "Saya klik tombolnya... gagal.", "Saya klik tombolnya! Lalu berhenti.",
            "I will not open the website.", "Saya klik tombolnya\n401 Unauthorized",
        ):
            with self.subTest(text=text):
                self.assertEqual(new_gate().select(text), text)

    def test_exact_dedup_is_turn_local_and_does_not_normalize_facts(self):
        gate = new_gate()
        text = "Password salah."
        self.assertEqual(gate.select(text), text)
        self.assertIsNone(gate.select(text))
        self.assertEqual(new_gate().select(text), text)
        for changed in ("Password salah!", " password salah. ",
                        "Saldo 100.", "Saldo 101.", "🔍 Saldo 102.", "🔍 Saldo 103."):
            self.assertEqual(gate.select(changed), changed)
        self.assertEqual(gate.select(text), text)

    def test_reemits_finding_after_another_meaningful_update(self):
        for first, intervening in (
            ("Finding A", "Finding B"),
            ("Login failed: session expired.", "Login succeeded; session restored."),
            ("Finding A", "Finding " + "x" * 4096),
        ):
            with self.subTest(first=first, intervening_length=len(intervening)):
                gate = new_gate()
                self.assertEqual(gate.select(first), first)
                self.assertEqual(gate.select(intervening), intervening)
                self.assertEqual(gate.select(first), first)
                self.assertIsNone(gate.select(first))

    def test_suppressed_routine_does_not_reset_consecutive_dedup(self):
        gate = new_gate()
        text = "Finding A"
        self.assertEqual(gate.select(text), text)
        self.assertIsNone(gate.select("Saya klik tombolnya."))
        self.assertIsNone(gate.select(text))

    def test_oversized_messages_are_not_retained(self):
        gate = new_gate()
        text = "Finding " + "x" * 4096
        self.assertEqual(gate.select(text), text)
        self.assertEqual(gate.select(text), text)
        boundary = "x" * 4096
        self.assertEqual(gate.select(boundary), boundary)
        self.assertIsNone(gate.select(boundary))


if __name__ == "__main__":
    unittest.main()