"""Real isolated Capstone worker tests; never fabricate decoded instructions."""
import importlib


def test_x86_call_conditional_return_page():
    module = importlib.import_module('zeline.reverse_disassembly')
    result = module.decode_instructions(bytes.fromhex('e802000000750190c3'), 'x86_64', address=0x1000, file_offset=20)
    assert result['bytes_consumed'] == 9
    assert result['stop_reason'] == 'end'
    instructions = result['instructions']
    assert [i['mnemonic'] for i in instructions] == ['call', 'jne', 'nop', 'ret']
    assert instructions[0]['flow'] == dict(kind='call', target=0x1007, conditional=False, fallthrough=0x1005)
    assert instructions[1]['flow'] == dict(kind='jump', target=0x1008, conditional=True, fallthrough=0x1007)
    assert instructions[3]['flow']['kind'] == 'return'
    assert instructions[1]['file_offset'] == 25
    assert result['blocks']
    assert result['xrefs'][0]['target'] == 0x1007
    assert all('edges' in block for block in result['blocks'])
    assert any('page' in note.lower() for note in result['limitations'])
    assert 'capstone' not in module.__dict__


import pytest


@pytest.mark.parametrize(('architecture', 'hexcode', 'kinds', 'targets'), [
    ('x86', 'ff d0 ff e0 c3', ['call', 'jump', 'return'], [None, None, None]),
    ('arm', '000000eb0000001a1eff2fe1', ['call', 'jump', 'return'], [0x1008, 0x100c, None]),
    ('thumb', '00b17047', ['jump', 'return'], [0x1004, None]),
    ('aarch64', '400000b440000836c0035fd6', ['jump', 'jump', 'return'], [0x1008, 0x100c, None]),
])
def test_architecture_control(architecture, hexcode, kinds, targets):
    from zeline.reverse_disassembly import decode_instructions
    result = decode_instructions(bytes.fromhex(hexcode), architecture, address=0x1000)
    assert [i['flow']['kind'] for i in result['instructions']] == kinds
    assert [i['flow']['target'] for i in result['instructions']] == targets
    if architecture in ('thumb', 'aarch64'):
        assert result['instructions'][0]['flow']['conditional'] is True



def test_private_decode_fails_closed_outside_worker():
    from zeline import reverse_disassembly as module
    with pytest.raises(RuntimeError, match='worker'):
        module._decode_worker({})


@pytest.mark.parametrize('kwargs', [
    {'address': True}, {'file_offset': False}, {'limit': True}, {'limit': 0},
    {'limit': 101}, {'address': -1}, {'address': 1 << 64},
    {'address': (1 << 64) - 1}, {'file_offset': (1 << 64) - 1},
    {'limit': 1.5}, {'endianness': 'big'}, {'architecture': 'mips'},
    {'should_stop': True},
])
def test_reject_invalid_parameters(kwargs):
    from zeline.reverse_disassembly import decode_instructions
    arguments = dict(architecture='x86_64', **kwargs) if 'architecture' not in kwargs else kwargs
    with pytest.raises(ValueError):
        decode_instructions(b'\x90\x90', **arguments)


def test_caps_truncation_no_skip_empty_and_uint64_boundary():
    from zeline.reverse_disassembly import decode_instructions
    with pytest.raises(ValueError):
        decode_instructions(b'\x90' * 4097, 'x86')
    with pytest.raises(ValueError):
        decode_instructions(bytearray(b'\x90'), 'x86')
    result = decode_instructions(b'\x90' * 4096, 'x86', limit=100)
    assert result['stop_reason'] == 'limit'
    assert result['bytes_consumed'] == 100
    for data, consumed in [(b'\x90\xe8\x00', 1), (b'\x0f', 0)]:
        result = decode_instructions(data, 'x86_64')
        assert result['stop_reason'] == 'undecodable'
        assert result['bytes_consumed'] == consumed
        assert len(result['instructions']) == consumed
    assert decode_instructions(b'', 'thumb')['stop_reason'] == 'end'
    result = decode_instructions(b'\x90', 'x86_64', address=(1 << 64) - 1)
    assert result['instructions'][0]['flow']['fallthrough'] is None


def test_cfg_external_unresolved_and_nonleader_midinstruction():
    from zeline.reverse_disassembly import decode_instructions
    result = decode_instructions(bytes.fromhex('eb01 e800000000 ffe0 c3'), 'x86_64', address=0x1000)
    # 0x1003 lies in the middle of CALL, never an alternative decode/leader.
    assert [b['address'] for b in result['blocks']] == [0x1000, 0x1002, 0x1007, 0x1009]
    assert result['blocks'][0]['edges'] == [dict(kind='jump', target=0x1003, scope='external')]
    assert result['blocks'][2]['edges'] == [dict(kind='jump', target=None, scope='unresolved')]


def test_big_endian_arm_and_conditional_return():
    from zeline.reverse_disassembly import decode_instructions
    result = decode_instructions(bytes.fromhex('112fff1e'), 'arm', endianness='big', address=0x1000)
    assert result['instructions'][0]['flow'] == dict(kind='return', target=None, conditional=True, fallthrough=0x1004)



def test_parent_never_imports_native_dependency():
    import subprocess
    import sys
    code = """
import sys
from zeline.reverse_disassembly import decode_instructions
assert 'capstone' not in sys.modules
decode_instructions(b'\\x90', 'x86')
assert 'capstone' not in sys.modules
"""
    subprocess.run([sys.executable, '-B', '-c', code], check=True, timeout=10)


def _track_workers(monkeypatch):
    from zeline import reverse_disassembly as module
    original = module.subprocess.Popen
    workers = []

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        workers.append(process)
        return process

    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    return module, workers


def _assert_clean(workers):
    assert workers
    for process in workers:
        assert process.poll() is not None
        assert all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))


def test_success_and_cancel_reap_close_pipes(monkeypatch):
    module, workers = _track_workers(monkeypatch)
    module.decode_instructions(b'\x90', 'x86')
    _assert_clean(workers)
    with pytest.raises(InterruptedError):
        module.decode_instructions(b'\x90', 'x86', should_stop=lambda: len(workers) > 1)
    _assert_clean(workers)
    assert not module._WORKER_LOCK.locked()


def test_worker_startup_deadline_reaps(monkeypatch):
    module, workers = _track_workers(monkeypatch)
    monkeypatch.setattr(module, 'TIMEOUT_SECONDS', 0.03)
    with pytest.raises(ValueError, match='time limit'):
        module.decode_instructions(b'\x90', 'x86')
    _assert_clean(workers)
    assert not module._WORKER_LOCK.locked()


def test_queue_deadline_and_cancellation(monkeypatch):
    module, workers = _track_workers(monkeypatch)
    module._WORKER_LOCK.acquire()
    try:
        monkeypatch.setattr(module, 'TIMEOUT_SECONDS', 0.03)
        with pytest.raises(ValueError, match='time limit'):
            module.decode_instructions(b'\x90', 'x86')
        with pytest.raises(InterruptedError):
            module.decode_instructions(b'\x90', 'x86', should_stop=lambda: True)
        assert not workers
    finally:
        module._WORKER_LOCK.release()


def test_real_workers_serialize(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    module, workers = _track_workers(monkeypatch)
    original = module.subprocess.Popen

    def spawn(*args, **kwargs):
        assert all(process.poll() is not None for process in workers)
        assert args[0][1:3] == ['-I', '-B']
        return original(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: module.decode_instructions(b'\x90', 'x86'), range(3)))
    assert all(result['instructions'][0]['mnemonic'] == 'nop' for result in results)
    assert len(workers) == 3
    _assert_clean(workers)


def test_early_worker_exit_fails_closed_and_closes_pipes(monkeypatch):
    module, workers = _track_workers(monkeypatch)
    original = module.subprocess.Popen

    def exit_early(args, **kwargs):
        return original([args[0], '-I', '-B', '-c', 'raise SystemExit(7)'], **kwargs)

    monkeypatch.setattr(module.subprocess, 'Popen', exit_early)
    with pytest.raises(ValueError, match='worker failure'):
        module.decode_instructions(b'\x90', 'x86')
    _assert_clean(workers)
    assert not module._WORKER_LOCK.locked()



def test_cancellation_polled_during_real_worker_startup(monkeypatch):
    module, workers = _track_workers(monkeypatch)
    # Cancel after the actual owned child exists; fast CPUs may finish before
    # an arbitrary wall-clock threshold, which is not a cancellation failure.
    with pytest.raises(InterruptedError):
        module.decode_instructions(b'\x90' * 4096, 'x86',
                                   should_stop=lambda: bool(workers))
    assert len(workers) == 1
    _assert_clean(workers)
    assert not module._WORKER_LOCK.locked()


def test_worker_memory_setup_precedes_native_import_and_fails_closed():
    import subprocess
    import sys
    from zeline import reverse_disassembly as module
    # Execute the real worker entry in an isolated child; make its trusted OS
    # limit helper unavailable, never replace any decoded response.
    code = """
import importlib.util
import json
import runpy
import sys
import types
original = importlib.util.module_from_spec
def module_from_spec(spec):
    module = original(spec)
    if spec.name == '_zeline_disassembly_limits':
        def fail(_):
            assert 'capstone' not in sys.modules
            raise OSError('OS memory limit unavailable')
        spec.loader = types.SimpleNamespace(exec_module=lambda module: setattr(module, 'set_memory_limit', fail))
    return module
importlib.util.module_from_spec = module_from_spec
sys.argv = [sys.argv[1], '--worker']
runpy.run_path(sys.argv[0], run_name='__main__')
assert 'capstone' not in sys.modules
"""
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, module.__file__],
                            capture_output=True, check=True, timeout=5)
    import json
    response = json.loads(result.stdout)
    assert response['ok'] is False
    assert response['kind'] == 'RuntimeError'
    assert 'no unsafe fallback' in response['error']


def test_worker_rejects_oversize_request():
    import json
    import subprocess
    import sys
    from zeline import reverse_disassembly as module
    result = subprocess.run([sys.executable, '-I', '-B', module.__file__, '--worker'],
                            input=b' ' * (module.MAX_REQUEST_BYTES + 1),
                            capture_output=True, check=True, timeout=5)
    assert 'request limit' in json.loads(result.stdout)['error']
    assert len(result.stdout) < module.MAX_RESPONSE_BYTES
