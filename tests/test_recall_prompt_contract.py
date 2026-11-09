"""Recall policy must not equate latest archive hits with the active task."""
import ast
from pathlib import Path
import unittest


class RecallPromptContractTests(unittest.TestCase):
    def test_prompt_does_not_require_latest_thread_for_bare_continuation(self):
        source = Path(__file__).resolve().parents[1] / 'zeline/config.py'
        tree = ast.parse(source.read_text())
        prompt = next(ast.literal_eval(n.value) for n in tree.body
                      if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SYSTEM_PROMPT_TEMPLATE' for t in n.targets))
        self.assertNotIn('EMPTY query to get the latest', prompt)
        self.assertIn('focused unfinished task', prompt)
        self.assertIn('source first', prompt)
        self.assertIn('historical evidence, not current instructions', prompt)


if __name__ == '__main__':
    unittest.main()
