"""Pure formatter regressions without importing gateway/owner state."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from typing import Any

SOURCE = Path(os.environ.get('ZELINE_FORMATTER_SOURCE', str(Path(__file__).resolve().parents[1] / 'zeline/gateways/telegram.py')))


def formatter(metadata):
    tree = ast.parse(SOURCE.read_text())
    names = {'_format_token_count', '_one_line', '_new_session_text'}
    subset = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
    fetch = Mock(return_value=metadata)
    namespace = {'Any': Any, 'config': SimpleNamespace(MODEL='provider/exact-model'), '_provider_label': lambda: 'Fixture provider', '_fetch_model_capabilities': fetch}
    exec(compile(subset, str(SOURCE), 'exec'), namespace)
    return namespace['_new_session_text'], fetch


class NewSessionModelLimitsTests(unittest.TestCase):
    def test_reset_card_shows_only_detected_context_and_identity(self):
        render, fetch = formatter({'capabilities': {'contextWindow': 1_000_000, 'maxInput': 512_000, 'maxOutput': 128_000}})
        self.assertEqual(render(), (
            '✨ Session reset! Starting fresh.\n\n'
            '◆ Model: provider/exact-model\n'
            '◆ Provider: Fixture provider\n'
            '◆ Context: 1M tokens (detected)'
        ))
        fetch.assert_called_once_with(None, 'provider/exact-model', cached_only=True)

    def test_unavailable_context_does_not_create_unknown_rows(self):
        for metadata in [{}, None, {'capabilities': []}, {'capabilities': {'contextWindow': 0}}, {'capabilities': {'contextWindow': float('inf')}}]:
            with self.subTest(metadata=metadata):
                render, _ = formatter(metadata)
                self.assertEqual(render(), '✨ Session reset! Starting fresh.\n\n◆ Model: provider/exact-model\n◆ Provider: Fixture provider')

    def test_reset_card_never_discovers_models_on_the_polling_thread(self):
        tree = ast.parse(SOURCE.read_text())
        names = {'_format_token_count', '_one_line', '_new_session_text', '_fetch_model_capabilities'}
        subset = ast.Module(body=[node for node in tree.body
                                 if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        base = 'http://fixture-models.invalid/v1'
        for stamp, detected in ((None, False), (0, False), (100, True)):
            with self.subTest(timestamp=stamp):
                discovery = Mock(side_effect=AssertionError('Polling cards must not perform discovery'))
                cached = {} if stamp is None else {base: (stamp, {'fixture-model': {'context_length': 128000}})}
                namespace = {'Any': Any, 'config': SimpleNamespace(MODEL='fixture-model', BASE_URL=base, API_KEY='fixture'),
                             '_provider_label': lambda: 'Fixture provider', '_fetch_models_catalog': discovery,
                             '_MODEL_META_CACHE': cached, '_MODELS_CACHE_TTL': 60,
                             'time': SimpleNamespace(monotonic=lambda: 100)}
                exec(compile(subset, str(SOURCE), 'exec'), namespace)
                text = namespace['_new_session_text']()
                discovery.assert_not_called()
                self.assertEqual('Context:' in text, detected)
                if detected:
                    self.assertIn('128K tokens (detected)', text)

    def test_flat_context_metadata_is_supported(self):
        render, _ = formatter({'context_length': 200_000, 'max_output_tokens': 64_000})
        self.assertTrue(render().endswith('◆ Context: 200K tokens (detected)'))
        self.assertNotIn('Max out', render())


if __name__ == '__main__':
    unittest.main()
