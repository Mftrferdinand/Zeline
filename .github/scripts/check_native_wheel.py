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
