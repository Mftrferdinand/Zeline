"""Compiler-produced PE evidence: build and inspect, never execute targets."""
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from zeline.reverse_engine import analyze


@unittest.skipUnless(all(shutil.which(x) for x in ('clang', 'lld-link', 'llvm-objdump')),
                     'clang, lld-link and llvm-objdump required for PE fixture')
class RealPEFixtureTests(unittest.TestCase):
    def test_exported_target_direct_call_and_indirect_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fixture.s'
            source.write_text('''.text
.globl main
main:
    call target
    call *slot(%rip)
    ret
.globl target
target:
    nop
    ret
.data
slot:
    .quad 0
''')
            obj = root / 'fixture.obj'
            binary = root / 'fixture.exe'
            for command in (['clang', '--target=x86_64-windows-msvc', '-c', str(source),
                             '-o', str(obj)],
                            ['lld-link', '/entry:main', '/subsystem:console', '/nodefaultlib',
                             '/export:target', '/out:' + str(binary), str(obj)]):
                subprocess.run(command, check=True, capture_output=True, timeout=20)
            listing = subprocess.run(['llvm-objdump', '-d', str(binary)],
                                     check=True, capture_output=True, text=True, timeout=20).stdout
            self.assertIn('callq', listing)
            binary_data = binary.read_bytes()
            result = analyze('references', 'fixture.exe', root, symbol='target')
            self.assertEqual(result['artifact']['sha256'], hashlib.sha256(binary_data).hexdigest())
            self.assertEqual(result['findings']['format'], 'pe')
            self.assertEqual(result['findings']['target']['declaration']['sources'][0]['table'],
                             'pe_exports')
            matches = result['findings']['matches']
            self.assertEqual(len(matches), 1)
            self.assertEqual(binary_data[matches[0]['file_offset']:
                                         matches[0]['file_offset'] + matches[0]['size']].hex(),
                             matches[0]['bytes_hex'])
            self.assertEqual(matches[0]['target'], result['findings']['target']['address'])
            self.assertGreaterEqual(result['findings']['unresolved_calls_jumps'], 1)
            self.assertFalse(result['findings']['coverage']['whole_program_complete'])
            self.assertFalse(result['findings']['exhausted'] and
                             result['findings']['coverage']['whole_program_complete'])

    def test_linked_import_iat_call_is_static_slot_not_runtime_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'provider.s').write_text('.text\n.globl ExitProcess\nExitProcess:\n retq\n')
            (root / 'caller.s').write_text(
                '.text\n.globl main\nmain:\n callq *__imp_ExitProcess(%rip)\n retq\n')
            for name in ('provider', 'caller'):
                subprocess.run(['clang', '--target=x86_64-windows-msvc', '-c',
                                str(root / f'{name}.s'), '-o', str(root / f'{name}.obj')],
                               check=True, capture_output=True, timeout=20)
            subprocess.run(['lld-link', '/dll', '/noentry', '/export:ExitProcess',
                            '/out:' + str(root / 'kernel32.dll'),
                            '/implib:' + str(root / 'kernel32.lib'),
                            str(root / 'provider.obj')],
                           check=True, capture_output=True, timeout=20)
            subprocess.run(['lld-link', '/entry:main', '/subsystem:console', '/nodefaultlib',
                            '/out:' + str(root / 'caller.exe'), str(root / 'caller.obj'),
                            str(root / 'kernel32.lib')],
                           check=True, capture_output=True, timeout=20)
            binary_data = (root / 'caller.exe').read_bytes()
            parsed = analyze('binary', 'caller.exe', root)['findings']
            imported = parsed['imports']['entries']
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0]['name'], 'ExitProcess')
            result = analyze('references', 'caller.exe', root,
                             import_iat_rva=imported[0]['iat_rva'])
            self.assertEqual(result['artifact']['sha256'], hashlib.sha256(binary_data).hexdigest())
            self.assertIsNone(result['findings']['target']['runtime_target'])
            self.assertIsNone(result['findings']['target']['runtime_provider'])
            match, = result['findings']['matches']
            self.assertEqual(match['static_import_iat_rva'], imported[0]['iat_rva'])
            self.assertEqual(match['operand_basis'], 'rip_relative')
            self.assertIsNone(match['target'])
            self.assertEqual(binary_data[match['file_offset']:
                                         match['file_offset'] + match['size']].hex(),
                             match['bytes_hex'])


if __name__ == '__main__':
    unittest.main()
