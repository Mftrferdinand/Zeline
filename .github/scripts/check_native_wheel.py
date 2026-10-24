"""Fresh wheel/native-tool smoke on real Linux, Windows and macOS runners."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

PROBE = r'''import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from unittest.mock import patch
from zeline import tools

root = Path.cwd()
package = Path(tools.__file__).resolve().parent
assert package.is_relative_to(Path(sys.prefix).resolve()), package
assert "capstone" not in sys.modules
workspace = root / "workspace"
workspace.mkdir()
code = bytes.fromhex("e80100000090c3ebfdffd0")
names = b"\0.text\0.shstrtab\0"
payload = 64 + 3 * 64 + len(names)
data = bytearray(payload + len(code))
data[:16] = b"\x7fELF\x02\x01\x01" + bytes(9)
struct.pack_into("<HHIQQQIHHHHHH", data, 16, 2, 62, 1, 0x401000, 0, 64, 0, 64, 0, 0, 64, 3, 2)
struct.pack_into("<IIQQQQIIQQ", data, 128, 1, 1, 6, 0x401000, payload, len(code), 0, 0, 1, 0)
struct.pack_into("<IIQQQQIIQQ", data, 192, 7, 3, 0, 0, 256, len(names), 0, 0, 1, 0)
data[256:payload] = names
data[payload:] = code
(workspace / "fixture.elf").write_bytes(data)
(workspace / "sentinel.py").write_text("from pathlib import Path\nPath('EXECUTED').write_text('bad')\ndef example(): pass\n", encoding="utf-8")
(workspace / "sentinel.js").write_text("require('fs').writeFileSync('EXECUTED','bad'); export function example() {}", encoding="utf-8")
executor = tools.ToolExecutor("cli:native-wheel", "workspace", workspace)
safe = tools.ToolExecutor("cli:native-wheel-denied", "safe", workspace)
assert executor.mcp is None
popen = subprocess.Popen
calls = []
def guarded(command, *args, **kwargs):
    assert list(command[:3]) == [sys.executable, "-I", "-B"], command
    worker = Path(command[3]).resolve()
    assert worker in (package / "reverse_disassembly.py", package / "reverse_source.py"), command
    assert command[4] == "--worker", command
    calls.append(worker.name)
    return popen(command, *args, **kwargs)
def run(action, path="fixture.elf", **options):
    result = executor.run("reverse_engineer", dict(action=action, path=path, **options))
    assert not result.startswith("ERROR"), result
    return json.loads(result)
def import_fixture(bits):
    """Inert standard PE table and two code bytestrings, never executable input."""
    base = 0x400000 if bits == 32 else 0x140000000
    width = bits // 8
    image = bytearray(0x40c)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3c, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    optional = 224 if bits == 32 else 240
    struct.pack_into("<HHIIIHH", image, 0x84, 0x14c if bits == 32 else 0x8664, 2, 0, 0, 0, optional, 0)
    struct.pack_into("<H", image, 0x98, 0x10b if bits == 32 else 0x20b)
    struct.pack_into("<I" if bits == 32 else "<Q", image, 0x98 + (28 if bits == 32 else 24), base)
    struct.pack_into("<II", image, 0x98 + 56, 0x3000, 0x200)
    minimum = 96 if bits == 32 else 112
    struct.pack_into("<I", image, 0x98 + minimum - 4, 16)
    struct.pack_into("<II", image, 0x98 + minimum + 8, 0x1000, 40)
    for index, (name, size, rva, raw, flags) in enumerate((
            (b".idata", 0x200, 0x1000, 0x200, 0), (b".text", 12, 0x2000, 0x400, 0x60000020))):
        struct.pack_into("<8sIIIIIIHHI", image, 0x98 + optional + index * 40,
                         name, size, rva, size, raw, 0, 0, 0, 0, flags)
    struct.pack_into("<IIIII", image, 0x200, 0x1100, 0, 0, 0x1080, 0x1100)
    image[0x280:0x28d] = b"KERNEL32.dll\0"
    fmt = "I" if bits == 32 else "Q"
    struct.pack_into("<" + fmt * 3, image, 0x300, 0x1180, (1 << (bits - 1)) | 7, 0)
    image[0x380:0x38e] = b"\0\0ExitProcess\0"
    for index, opcode in enumerate((b"\xff\x15", b"\xff\x25")):
        slot = 0x1100 + index * width
        operand = base + slot if bits == 32 else slot - (0x2000 + index * 6 + 6)
        image[0x400 + index * 6:0x406 + index * 6] = opcode + struct.pack("<I" if bits == 32 else "<i", operand)
    return image

with patch("subprocess.Popen", side_effect=guarded):
    inspected = run("inspect")
    assert inspected["artifact"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert not calls
    disassembled = run("disassemble", limit=100)
    assert disassembled["findings"]["instructions"][0]["bytes_hex"] == "e801000000"
    reference = run("references", target_address=0x401006, limit=100)
    assert [row["kind"] for row in reference["findings"]["matches"]] == ["call", "jump"]
    assert reference == run("references", target_address=0x401006, limit=100)
    assert reference["findings"]["unresolved_calls_jumps"] == 1
    assert reference["findings"]["coverage"]["whole_program_complete"] is False
    for bits in (32, 64):
        pe = import_fixture(bits)
        filename = f"imports-{bits}.dll"
        (workspace / filename).write_bytes(pe)
        page = run("references", path=filename, import_iat_rva=0x1100, limit=1)
        finding = page["findings"]
        assert page["artifact"]["sha256"] == hashlib.sha256(pe).hexdigest()
        assert finding["target"]["import"]["name"] == "ExitProcess"
        assert finding["target"]["runtime_target"] is None
        assert finding["target"]["runtime_provider"] is None
        assert finding["matches"][0]["target"] is None
        assert finding["unresolved_calls_jumps"] == 1
        assert finding["next_byte_offset"] == 0x406
        tail = run("references", path=filename, import_iat_rva=0x1100,
                   byte_offset=finding["next_byte_offset"], limit=1)
        assert tail["artifact"] == page["artifact"]
        assert tail["findings"]["matches"] == []
        ordinal = run("references", path=filename, import_iat_rva=0x1100 + bits // 8,
                      byte_offset=0x406, limit=1)["findings"]
        assert ordinal["target"]["import"]["ordinal"] == 7
        assert ordinal["matches"][0]["kind"] == "jump"
        assert ordinal["matches"][0]["target"] is None
        assert ordinal["coverage"]["whole_program_complete"] is False
        assert ordinal["exhausted"] is True
    run("source", path="sentinel.py")
    run("source", path="sentinel.js")
    before = len(calls)
    assert safe.run("reverse_engineer", dict(action="references", path="fixture.elf", target_address=0)).startswith("ERROR")
    assert len(calls) == before
assert not (workspace / "EXECUTED").exists()
assert not (root / "EXECUTED").exists()
assert "capstone" not in sys.modules
print(json.dumps(dict(status="passed", package=str(package), workers=calls, targets_executed=False)))
'''


def main():
    with tempfile.TemporaryDirectory(prefix="zeline-native-wheel-") as temporary:
        root = Path(temporary)
        if "--probe-current" in sys.argv:
            python = Path(sys.executable)
        else:
            wheel, = Path("dist").glob("*.whl")
            environment = root / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([str(python), "-m", "pip", "install", str(wheel.resolve())], check=True, timeout=300)
        state = root / "state"
        state.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("ZELINE_")}
        env.update(ZELINE_HOME=str(state), PYTHONIOENCODING="utf-8")
        probe = root / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        subprocess.run([str(python), "-I", "-B", str(probe)], cwd=root, env=env, check=True, timeout=60)
        console = python.parent / ("zeline.exe" if os.name == "nt" else "zeline")
        subprocess.run([str(console), "--version"], cwd=root, env=env, check=True, timeout=20)
        subprocess.run([str(console), "--help"], cwd=root, env=env, check=True, timeout=20,
                       stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
