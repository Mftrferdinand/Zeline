"""Diagnostic only: compare Darwin allocator settings without changing worker caps."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def main():
    if sys.platform != 'darwin':
        print('ALLOCATOR_DIAGNOSTIC_NOT_APPLICABLE')
        return
    from zeline import reverse_disassembly as decoder
    original = subprocess.Popen
    trusted = Path(decoder.__file__).resolve()
    modes = [('control', {'MallocSpaceEfficient': '0'}),
             ('nano-off', {'MallocNanoZone': '0', 'MallocSpaceEfficient': '0'}),
             ('space-efficient', {'MallocSpaceEfficient': '1'}),
             ('nano-off-space-efficient', {'MallocNanoZone': '0', 'MallocSpaceEfficient': '1'})]
    for name, overrides in modes:
        for repeat in range(10):
            observed = {}

            def launch(command, *args, **kwargs):
                assert command[1:3] == ['-I', '-B']
                assert Path(command[3]).resolve() == trusted
                assert command[4:] == ['--worker']
                kwargs['env'] = {**kwargs['env'], **overrides}
                child = original(command, *args, **kwargs)
                communicate = child.communicate

                def capture(*args, **kwargs):
                    stdout, stderr = communicate(*args, **kwargs)
                    observed.update(returncode=child.returncode,
                                    stdout_bytes=len(stdout or b''),
                                    stderr_prefix=(stderr or b'')[:4096].decode('utf-8', 'replace'))
                    return stdout, stderr

                child.communicate = capture
                return child

            with patch.object(decoder.subprocess, 'Popen', side_effect=launch):
                try:
                    result = decoder.decode_instructions(bytes.fromhex('e806000000c3'),
                                                         'x86_64', address=4096, limit=8)
                    observed.update(ok=True, result_keys=sorted(result))
                except Exception as error:
                    observed.update(ok=False, error_kind=type(error).__name__, error=str(error)[:300])
            print('NATIVE_ALLOCATOR_PROBE ' + json.dumps(dict(mode=name, repeat=repeat, **observed)), flush=True)
    # This experiment is not a release gate. The real native preflight/full
    # suite still runs afterward with unchanged production worker settings.


if __name__ == '__main__':
    main()
