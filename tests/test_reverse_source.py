"""Parser-backed, inert source inspection contracts."""
import unittest


class SourceInspectionTests(unittest.TestCase):
    def test_javascript_locations_do_not_allocate_native_point_objects(self):
        # CI CPython 3.10 crashes in native positional access. Byte spans are
        # sufficient and preserve LF/CRLF and UTF-8 line accounting exactly.
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from zeline import reverse_source as source
        data = "// café\r\nclass Box {}\n".encode('utf-8')
        name_start = data.index(b'Box')

        class Node:
            type = 'class_declaration'
            has_error = False
            start_byte = data.index(b'class')
            end_byte = len(data)

            @property
            def start_point(self):
                raise AssertionError('native Point allocation must not be used')

            @property
            def end_point(self):
                raise AssertionError('native Point allocation must not be used')

            def child_by_field_name(self, name):
                return SimpleNamespace(start_byte=name_start, end_byte=name_start + 3)

            def walk(self):
                return SimpleNamespace(node=self, goto_first_child=lambda: False,
                                       goto_next_sibling=lambda: False, goto_parent=lambda: False)

        modules = {
            'tree_sitter': SimpleNamespace(Language=lambda capsule: capsule,
                                          Parser=lambda language: SimpleNamespace(
                                              parse=lambda value: SimpleNamespace(root_node=Node()))),
            'tree_sitter_javascript': SimpleNamespace(language=lambda: object()),
        }
        with patch.dict(sys.modules, modules):
            result = source._inspect_source_local(data, 'javascript')
        self.assertEqual(result['definitions'], [dict(name='Box', kind='class_declaration',
                         line=2, end_line=3, start_byte=data.index(b'class'), end_byte=len(data))])
        for start, end in [(-1, 0), (0, len(data) + 1), (4, 3)]:
            with self.subTest(start=start, end=end), patch.dict(sys.modules, modules):
                Node.start_byte, Node.end_byte = start, end
                with self.assertRaisesRegex(ValueError, 'invalid byte span'):
                    source._inspect_source_local(data, 'javascript')

    def test_cancel_after_worker_exit_closes_all_pipes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from zeline import reverse_source as module
        children = []
        original = module.subprocess.Popen
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        def cancelled():
            if children:
                children[0].wait(timeout=3)
                return True
            return False
        with TemporaryDirectory() as directory:
            helper = Path(directory) / 'trusted_early_exit_worker.py'
            helper.write_text('print("early exit")')
            with patch.object(module, '__file__', str(helper)), patch.object(module.subprocess, 'Popen', side_effect=launch):
                with self.assertRaises(InterruptedError):
                    module.inspect_source(b'def f(): pass', 'python', should_stop=cancelled)
        self.assertEqual(len(children), 1)
        for stream in (children[0].stdin, children[0].stdout, children[0].stderr):
            self.assertTrue(stream.closed)

    def test_cancellation_kills_and_reaps_owned_worker(self):
        from unittest.mock import patch
        from zeline import reverse_source as module
        children = []
        original = module.subprocess.Popen
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(module.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(InterruptedError):
                module.inspect_source(b'def f(): pass', 'python', should_stop=lambda: bool(children))
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_deadline_kills_stalled_trusted_worker(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from zeline import reverse_source as module
        children = []
        original = module.subprocess.Popen
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with TemporaryDirectory() as directory:
            helper = Path(directory) / 'trusted_stalled_worker.py'
            helper.write_text('import time; time.sleep(30)')
            with patch.object(module, '__file__', str(helper)), patch.object(module, 'SOURCE_TIMEOUT_SECONDS', 0.2), patch.object(module.subprocess, 'Popen', side_effect=launch):
                with self.assertRaisesRegex(ValueError, 'time limit'):
                    module.inspect_source(b'def f(): pass', 'python')
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_concurrent_calls_serialize_owned_workers(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from zeline import reverse_source as module
        children = []
        original = module.subprocess.Popen
        def launch(*args, **kwargs):
            self.assertFalse(any(child.poll() is None for child in children))
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(module.subprocess, 'Popen', side_effect=launch):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(module.inspect_source, b'def f(): pass', 'python') for _ in range(2)]
                for future in futures:
                    self.assertEqual(future.result(timeout=8)['definitions'][0]['name'], 'f')
        self.assertEqual(len(children), 2)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_python_utf8_bom_offsets_refer_to_original_bytes(self):
        from zeline.reverse_source import inspect_source
        source = b'\xef\xbb\xbfimport os\ndef f(): pass\n'
        result = inspect_source(source, 'python')
        self.assertEqual(result['imports'][0]['start_byte'], 3)
        self.assertEqual(result['definitions'][0]['start_byte'], source.index(b'def'))
        self.assertEqual(result['imports'][0]['line'], 1)

    def test_ast_parser_never_runs_in_agent_process(self):
        from unittest.mock import patch
        from zeline.reverse_source import inspect_source
        with patch('ast.parse', side_effect=AssertionError('parser ran inside the agent')):
            result = inspect_source(b'def f(): pass', 'python')
        self.assertEqual(result['definitions'][0]['name'], 'f')

    def test_dense_python_is_stopped_by_worker_memory_limit(self):
        from zeline import reverse_source as module
        self.assertTrue(hasattr(module, 'SOURCE_MEMORY_BYTES'),
                        'An enforced worker memory limit is required before this repro runs')
        with self.assertRaisesRegex(ValueError, 'memory|resource|limit'):
            module.inspect_source(b'x=0\n' * 200000, 'python')

    def test_python_ast_imports_definitions_offsets_and_no_execution(self):
        from zeline.reverse_source import inspect_source
        source = ('# café\nimport os as operating\nfrom .tools import run\n'
                  'raise RuntimeError("must never execute")\n'
                  'async def worker():\n    pass\nclass Box:\n    def method(self): pass\n').encode()
        result = inspect_source(source, 'python')
        self.assertEqual(set(result), {'language', 'parser', 'imports', 'definitions', 'limitations'})
        self.assertEqual(result['parser'], 'ast')
        self.assertEqual([i['name'] for i in result['imports']], ['os', 'run'])
        self.assertEqual(result['imports'][0]['alias'], 'operating')
        self.assertEqual(result['imports'][1]['module'], '.tools')
        self.assertEqual([d['name'] for d in result['definitions']], ['worker', 'Box', 'method'])
        worker = result['definitions'][0]
        self.assertEqual(worker['line'], 5)
        self.assertEqual(worker['start_byte'], source.index(b'async def'))
        self.assertTrue(source[worker['start_byte']:worker['end_byte']].startswith(b'async def'))
        self.assertTrue(any('semantic' in x.lower() for x in result['limitations']))

    def test_javascript_modern_syntax_inventory_without_execution(self):
        from zeline.reverse_source import inspect_source
        source = '''// café
import main, {read as load} from "pkg";
import "side-effects";
export {remote as renamed} from "other";
export * from "more";
export async function worker(x = {}) { return x?.value ?? 0; }
export default class Box { #value = 1; static { this.ready = true; } method() {} }
const arrow = async (x) => x ** 2;
const named = function inner() {};
const C = class Hidden {};
throw new Error("must never execute");
'''.encode()
        result = inspect_source(source, 'javascript')
        self.assertEqual(result['parser'], 'tree-sitter-javascript')
        self.assertEqual(result['language'], 'javascript')
        self.assertEqual([i['module'] for i in result['imports']], ['pkg', 'side-effects', 'other', 'more'])
        names = {d['name'] for d in result['definitions']}
        self.assertTrue({'worker', 'Box', 'method', 'arrow', 'named', 'C'} <= names)
        self.assertTrue(any(d['kind'] == 'export' for d in result['definitions']))
        worker = next(d for d in result['definitions'] if d['name'] == 'worker')
        self.assertEqual(worker['start_byte'], source.index(b'async function'))
        for item in result['imports'] + result['definitions']:
            self.assertTrue(0 <= item['start_byte'] < item['end_byte'] <= len(source))
            self.assertGreaterEqual(item['line'], 1)

    def test_rejects_invalid_input(self):
        from zeline.reverse_source import inspect_source
        for language, data in [('python', b'def broken('), ('javascript', b'function broken( {'),
                               ('javascript', b'const x = ;'), ('python', b'\xff'),
                               ('javascript', b'\xff'), ('ruby', b'')]:
            with self.subTest(language=language, data=data), self.assertRaises(ValueError):
                inspect_source(data, language)

    def test_caps_bytes_nodes_and_findings(self):
        from unittest.mock import patch
        from zeline import reverse_source as module
        for language, source in [('python', b'def f(): pass\n' * 40),
                                 ('javascript', b'function f() {}\n' * 40)]:
            with self.subTest(language=language):
                with patch.object(module, 'MAX_SOURCE_BYTES', 16):
                    with self.assertRaisesRegex(ValueError, 'byte'):
                        module.inspect_source(source, language)
                with patch.object(module, 'MAX_FINDINGS', 3):
                    result = module.inspect_source(source, language)
                    self.assertLessEqual(len(result['imports']) + len(result['definitions']), 3)
                    self.assertTrue(any('truncat' in x.lower() for x in result['limitations']))
                with patch.object(module, 'MAX_NODES', 3):
                    result = module.inspect_source(source, language)
                    self.assertTrue(any('node' in x.lower() and 'truncat' in x.lower()
                                        for x in result['limitations']))

    def test_no_filesystem_execution(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from zeline.reverse_source import inspect_source
        with TemporaryDirectory() as directory:
            sentinel = Path(directory) / 'executed'
            inspect_source(f"open({str(sentinel)!r}, 'w').write('bad')".encode(), 'python')
            inspect_source(f"require('fs').writeFileSync({str(sentinel)!r}, 'bad');".encode(), 'javascript')
            self.assertFalse(sentinel.exists())

    def test_python_crlf_and_cr_only_offsets(self):
        from zeline.reverse_source import inspect_source
        for newline in (b'\r\n', b'\r', b'\n'):
            source = newline.join([b'# first', b'import os', b'def f(): pass'])
            with self.subTest(newline=newline):
                result = inspect_source(source, 'python')
                self.assertEqual(result['definitions'][0]['start_byte'], source.index(b'def'))

    def test_labels_are_bounded(self):
        from zeline.reverse_source import inspect_source, MAX_LABEL_BYTES
        for language, source in [('python', ('def ' + 'a' * 5000 + '(): pass').encode()),
                                 ('javascript', ('function ' + 'a' * 5000 + '() {}').encode())]:
            result = inspect_source(source, language)
            self.assertLessEqual(len(result['definitions'][0]['name'].encode()), MAX_LABEL_BYTES)
            self.assertTrue(any('Labels truncated' in x for x in result['limitations']))

    def test_syntax_errors_not_hidden_by_inventory_caps(self):
        from unittest.mock import patch
        from zeline import reverse_source as module
        for language, source in [('python', b'def f(): pass\ndef broken('),
                                 ('javascript', b'function f() {} function broken(')]:
            with patch.object(module, 'MAX_NODES', 1), patch.object(module, 'MAX_FINDINGS', 1):
                with self.assertRaises(ValueError):
                    module.inspect_source(source, language)

    def test_empty_and_nonbyte_inputs(self):
        from zeline.reverse_source import inspect_source
        for language in ('python', 'javascript'):
            result = inspect_source(b'', language)
            self.assertEqual(result['imports'], [])
            self.assertEqual(result['definitions'], [])
            with self.assertRaises(ValueError):
                inspect_source('not bytes', language)


if __name__ == '__main__':
    unittest.main()
