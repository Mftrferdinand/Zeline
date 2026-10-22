"""Focused, offline tests for the chat composer."""
import io
import unittest
from unittest.mock import patch

from zeline.cli_input import ChatInput


class ChatInputTests(unittest.TestCase):
    def test_plain_input_preserves_spaces_and_resolves_builtin_dynamically(self):
        composer = ChatInput(interactive=False, stream=io.StringIO())
        with patch('builtins.input', return_value='  hello  ') as reader:
            self.assertEqual(composer.read(), '  hello  ')
        reader.assert_called_once()

    def test_trailing_backslash_continues_a_multiline_prompt(self):
        lines = iter(['first line \\', 'second line'])
        composer = ChatInput(
            interactive=False,
            input_fn=lambda _prompt='': next(lines),
            stream=io.StringIO(),
        )
        self.assertEqual(composer.read(), 'first line \nsecond line')

    def test_numeric_choice_maps_to_its_option_and_free_text_passes_through(self):
        answers = iter(['2', 'my own answer'])
        composer = ChatInput(
            interactive=False,
            input_fn=lambda _prompt='': next(answers),
            stream=io.StringIO(),
        )
        self.assertEqual(composer.choose('Pick', ['alpha', 'beta']), 'beta')
        self.assertEqual(composer.choose('Pick', ['alpha', 'beta']), 'my own answer')

    def test_out_of_range_number_is_not_guessed_as_an_option(self):
        answers = iter(['9', 'own'])
        composer = ChatInput(
            interactive=False,
            input_fn=lambda _prompt='': next(answers),
            stream=io.StringIO(),
        )
        self.assertEqual(composer.choose('Pick', ['alpha', 'beta']), 'own')


if __name__ == '__main__':
    unittest.main()
