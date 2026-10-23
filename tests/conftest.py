"""Opt-in CI diagnostics for trusted native workers; never execute target files."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def native_worker_failure_diagnostics(monkeypatch):
    if os.environ.get('ZELINE_CI_WORKER_DIAGNOSTICS') != '1':
        return
    trusted = {str((Path(__file__).resolve().parents[1] / 'zeline' / name).resolve())
               for name in ('reverse_source.py', 'reverse_disassembly.py')}
    original = subprocess.Popen.communicate

    def observed(process, *args, **kwargs):
        result = original(process, *args, **kwargs)
        command = process.args
        if not isinstance(command, (list, tuple)) or len(command) < 4:
            return result
        if str(Path(command[3]).resolve()) not in trusted:
            return result
        stdout, stderr = result
        try:
            response = json.loads(stdout)
        except (ValueError, TypeError, UnicodeError):
            response = {}
        if process.returncode or response.get('ok') is False:
            inherited = None
            if os.name != 'nt':
                import resource
                inherited = {name: resource.getrlimit(getattr(resource, name))
                             for name in ('RLIMIT_DATA', 'RLIMIT_AS')}
            diagnostic = dict(worker=Path(command[3]).name, returncode=process.returncode,
                              stdout_bytes=len(stdout or b''), error_kind=response.get('kind'),
                              error=str(response.get('error', ''))[:300],
                              stderr_prefix=(stderr or b'')[:4096].decode('utf-8', 'replace'),
                              parent_limits=inherited, platform=sys.platform,
                              allocator_env={key: os.environ.get(key) for key in
                                             ('MallocNanoZone', 'MallocSpaceEfficient', 'PYTHONMALLOC')})
            print('NATIVE_WORKER_DIAGNOSTIC ' + json.dumps(diagnostic), file=sys.stderr, flush=True)
        return result

    monkeypatch.setattr(subprocess.Popen, 'communicate', observed)
