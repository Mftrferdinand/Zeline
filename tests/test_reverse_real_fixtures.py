"""Known-answer checks against real linker output; targets are never executed.

These tests exercise the native static engine with artifacts produced by clang
and lld, then compare decoder bytes and direct targets with llvm-objdump's
assembly view. They are skipped on hosts without the required toolchain.
"""
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from zeline.reverse_engine import analyze
from zeline.reverse_symbol_navigation import resolve_code_symbol


CLANG = shutil.which("clang")
LLD = shutil.which("ld.lld")
OBJDUMP = shutil.which("llvm-objdump")
TOOLCHAIN_AVAILABLE = bool(CLANG and LLD and OBJDUMP)


X86_ASSEMBLY = r""".section .text
.globl _start
.type _start,@function
_start:
    call target
    mov $60, %rax
    xor %edi, %edi
    syscall
.size _start, .-_start

.type target,@function
target:
    nop
    ret
.size target, .-target

.globl imported_like
.type imported_like,@function
imported_like:
    lea msg(%rip), %rdi
    ret
.size imported_like, .-imported_like

.section .rodata
msg:
    .asciz "static-fixture"

.section .data
.globl data_marker
.type data_marker,@object
.size data_marker, 8
data_marker:
    .quad 0x1122334455667788

.section .note.GNU-stack,"",@progbits
"""

AARCH64_ASSEMBLY = r""".text
.globl _start
.type _start,%function
_start:
    bl target
    mov x0, #0
    mov x8, #93
    svc #0
.size _start, .-_start

.type target,%function
target:
    nop
    ret
.size target, .-target

.section .note.GNU-stack,"",@progbits
"""


@unittest.skipUnless(TOOLCHAIN_AVAILABLE, "clang, ld.lld and llvm-objdump are required")
class RealReverseFixtureTests(unittest.TestCase):
    """Real ELF fixtures validate bytes without running the produced files."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    @staticmethod
    def _run(command, cwd):
        result = subprocess.run(command, cwd=cwd, check=False,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        if result.returncode:
            raise AssertionError(
                f"fixture command failed ({result.returncode}): {' '.join(map(str, command))}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}")
        return result.stdout

    def _build(self, name, assembly, target, linker_machine):
        source = self.root / f"{name}.s"
        obj = self.root / f"{name}.o"
        binary = self.root / f"{name}.elf"
        source.write_text(assembly, encoding="utf-8")
        self._run([CLANG, "-c", "--target=" + target, str(source), "-o", str(obj)], self.root)
        self._run([LLD, "-flavor", "gnu", "-m", linker_machine,
                   "-o", str(binary), str(obj)], self.root)
        return binary

    def test_real_x86_64_elf_bytes_symbols_and_incoming_call(self):
        binary = self._build("x86_known", X86_ASSEMBLY,
                             "x86_64-unknown-linux-gnu", "elf_x86_64")
        listing = self._run([OBJDUMP, "-d", str(binary)], self.root)
        self.assertIn("e8 0b 00 00 00", listing)
        self.assertIn("90", listing)

        first = analyze("disassemble", binary.name, self.root, symbol="_start", limit=1)
        findings = first["findings"]
        self.assertEqual(findings["instructions"][0]["bytes_hex"], "e80b000000")
        self.assertEqual(findings["instructions"][0]["mnemonic"], "call")
        self.assertEqual(findings["instructions"][0]["file_offset"], findings["byte_offset"])

        target = analyze("disassemble", binary.name, self.root, symbol="target", limit=2)
        target_findings = target["findings"]
        target_address = target_findings["address"]
        self.assertEqual(findings["instructions"][0]["flow"]["target"], target_address)
        self.assertEqual(target_findings["instructions"][0]["bytes_hex"], "90")
        self.assertEqual(target_findings["instructions"][1]["bytes_hex"], "c3")

        references = analyze("references", binary.name, self.root,
                             target_address=target_address, limit=100)["findings"]
        self.assertEqual(len(references["matches"]), 1)
        self.assertEqual(references["matches"][0]["kind"], "call")
        self.assertEqual(references["matches"][0]["target"], target_address)
        self.assertEqual(first["artifact"]["sha256"], hashlib.sha256(binary.read_bytes()).hexdigest())

        with self.assertRaisesRegex(ValueError, "no eligible"):
            resolve_code_symbol(binary.read_bytes(), "data_marker")

    def test_real_aarch64_elf_direct_branch_and_instruction_bytes(self):
        binary = self._build("aarch64_known", AARCH64_ASSEMBLY,
                             "aarch64-none-elf", "aarch64elf")
        listing = self._run([OBJDUMP, "-d", str(binary)], self.root)
        self.assertIn("94000004", listing)
        self.assertIn("d503201f", listing)

        first = analyze("disassemble", binary.name, self.root, symbol="_start", limit=1)
        instruction = first["findings"]["instructions"][0]
        self.assertEqual(first["findings"]["architecture"], "aarch64")
        self.assertEqual(instruction["bytes_hex"], "04000094")
        self.assertEqual(instruction["mnemonic"], "bl")

        target = analyze("disassemble", binary.name, self.root, symbol="target", limit=2)
        self.assertEqual(instruction["flow"]["target"], target["findings"]["address"])
        self.assertEqual(target["findings"]["instructions"][0]["bytes_hex"], "1f2003d5")
        # llvm-objdump prints the A64 word; the engine reports file bytes.
        self.assertEqual(target["findings"]["instructions"][1]["bytes_hex"], "c0035fd6")

    def test_disassembly_worker_receives_bytes_not_target_path(self):
        binary = self._build("x86_worker", X86_ASSEMBLY,
                             "x86_64-unknown-linux-gnu", "elf_x86_64")
        from zeline import reverse_disassembly
        original = reverse_disassembly.subprocess.Popen
        commands = []

        def launch(*args, **kwargs):
            commands.append(list(args[0]))
            return original(*args, **kwargs)

        with patch.object(reverse_disassembly.subprocess, "Popen", side_effect=launch):
            result = analyze("disassemble", binary.name, self.root, symbol="_start", limit=1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(commands), 1)
        command_text = " ".join(commands[0])
        self.assertIn("reverse_disassembly.py", command_text)
        self.assertNotIn(str(binary), command_text)
        self.assertNotIn("x86_worker.elf", command_text)


if __name__ == "__main__":
    unittest.main()