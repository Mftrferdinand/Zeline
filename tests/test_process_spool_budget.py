"""Foreground preservation must bound disk spool as well as RAM."""
import importlib
import os
import subprocess
import sys
from unittest import mock


def test_foreground_spool_has_a_disk_budget_and_explicit_handoff_omission(tmp_path):
    output = importlib.import_module("zeline.process_output")
    # The Windows reader requires a real process pipe, not a regular disk file.
    # This trusted fixture emits fixed bytes; it never runs an input artifact.
    process = subprocess.Popen(
        [sys.executable, '-I', '-B', '-c',
         "import sys; sys.stdout.buffer.write(b'first\\n' + b'x' * 65536 + b'\\nlast\\n')"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    target = tmp_path / "promoted.log"
    with mock.patch.object(output, "SPOOL_BUDGET", 16_384, create=True):
        capture = output.ProcessOutput(process)
        try:
            assert capture.done.wait(3), "finite fixture reader did not finish"
            assert os.fstat(capture.spool.fileno()).st_size <= 16_384
            handle = target.open("wb", buffering=0)
            capture.promote(handle)
            text = target.read_bytes()
            assert b"TRUNCATED" in text
            assert text.endswith(b"last\n")
        finally:
            capture.finish()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdout.close()
