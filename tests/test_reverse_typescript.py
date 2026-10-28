"""First-party bounded TypeScript/TSX syntax evidence on inert fixtures."""
import tempfile
import importlib.util
import unittest
from pathlib import Path

from zeline.reverse_engine import analyze


@unittest.skipUnless(importlib.util.find_spec('tree_sitter_typescript'),
                     'optional reverse-ts grammar not installed')
class TypeScriptReverseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    def test_typescript_imports_reexports_and_declarations(self):
        source = ('import type { Shape } from "./types";\n'
                  'export { value } from "./values";\n'
                  'export function build(x: Shape): number { return 1; }\n'
                  'type Alias = Shape;\ninterface Contract { x: number }\n')
        (self.workspace / 'fixture.ts').write_text(source)
        result = analyze('source', 'fixture.ts', self.workspace)
        facts = result['findings']
        self.assertEqual(facts['language'], 'typescript')
        self.assertEqual([(x['module'], x['type_only']) for x in facts['imports']],
                         [('./types', True), ('./values', False)])
        self.assertEqual({x['name'] for x in facts['definitions'] if x['name'] != 'export'},
                         {'build', 'Alias', 'Contract'})
        self.assertEqual(facts['imports'][0]['start_byte'], source.encode().index(b'import'))

    def test_tsx_parses_jsx_without_executing_source(self):
        sentinel = self.workspace / 'executed'
        source = ('import Widget from "widget";\n'
                  f'export function View() {{ require("fs").writeFileSync("{sentinel}", "bad"); '
                  'return <Widget />; }\n')
        (self.workspace / 'view.tsx').write_text(source)
        facts = analyze('source', 'view.tsx', self.workspace)['findings']
        self.assertEqual(facts['language'], 'tsx')
        self.assertEqual(facts['imports'][0]['module'], 'widget')
        self.assertIn('View', [row['name'] for row in facts['definitions']])
        self.assertFalse(sentinel.exists())

    def test_malformed_typescript_fails_without_partial_evidence(self):
        (self.workspace / 'bad.ts').write_text('export function broken( {')
        with self.assertRaisesRegex(ValueError, 'Malformed TypeScript'):
            analyze('source', 'bad.ts', self.workspace)


if __name__ == '__main__':
    unittest.main()
