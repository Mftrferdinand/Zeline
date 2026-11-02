"""Formatting a report list is not permission to block on an approval callback."""
import unittest
from zeline.question_picker import branching_question


class PickerIntentTests(unittest.TestCase):
    def test_informational_numbered_and_lettered_lists_remain_plain_replies(self):
        for text in (
            'What does the report contain?\n1. Summary\n2. Detailed findings',
            'What does the report contain?\nA. Summary\nB. Detailed findings',
            'Apa isi laporan?\n1. Ringkasan\n2. Temuan lengkap',
            'When was the report updated?\n1. Yesterday\n2. Today',
            'Kapan laporan diubah?\n1. Kemarin\n2. Hari ini',
        ):
            with self.subTest(text=text):
                self.assertIsNone(branching_question(text))

    def test_explicit_choice_still_uses_native_picker(self):
        self.assertEqual(branching_question('Choose a report?\n1. Summary\n2. Detailed findings'),
                         ('Choose a report?', ('Summary', 'Detailed findings')))
