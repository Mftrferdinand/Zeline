"""Offline integration tests for snippet-only native tool dispatch."""
import json
import os
import shlex
import subprocess
import sys
import threading
import time

import pytest

from zeline import tools


def python_command(source):
    args = [sys.executable, '-c', source]
    return subprocess.list2cmdline(args) if os.name == 'nt' else shlex.join(args)


@pytest.fixture
def executor(tmp_path):
    return tools.ToolExecutor('cli:bridge-test', 'full', workspace=tmp_path)


def test_real_files_shell_and_audit(executor, monkeypatch):
    audit, events = [], []
    monkeypatch.setattr(executor, '_audit', lambda n, a, r: audit.append((n, a, r)))
    executor._nested_tool_callback = lambda n, a: events.append((n, a))
    shell_command = python_command("print('shell-real')")
    result = executor.run('execute_code', {'code': f'''
from zeline_tools import read_file, write_file, search_files, patch, terminal
import json
results = [write_file('note.txt', 'hello bridge\\n'),
           read_file(path='note.txt'),
           patch('note.txt', old_string='hello', new_string='real'),
           search_files('real'),
           terminal({shell_command!r})]
print(json.dumps(results))
'''})
    assert result.startswith('exit=0\n'), result
    data = json.loads(result.split('\n', 1)[1])
    assert all(x['success'] for x in data), data
    assert 'hello bridge' in data[1]['output']
    assert 'shell-real' in data[4]['output']
    assert (executor.workspace / 'note.txt').read_text() == 'real bridge\n'
    names = ['write_file', 'read_file', 'patch_file', 'search_files', 'run_shell']
    assert [x[0] for x in events] == names
    assert [x[0] for x in audit] == names + ['execute_code']
    print('REAL_BRIDGE_OUTPUT', result)


def test_denied_tool_no_callback(executor, monkeypatch):
    executor._disabled_tools |= {'write_file'}
    executor._native_defs = tuple(d for d in executor._native_defs if d.name != 'write_file')
    events = []
    executor._nested_tool_callback = lambda *a: events.append(a)
    result = executor.run('execute_code', {'code': "from zeline_tools import write_file; print(write_file('no.txt', 'no'))"})
    assert 'disabled' in result or 'not registered' in result
    assert not (executor.workspace / 'no.txt').exists()
    assert events == []


def test_profile_denied(tmp_path):
    executor = tools.ToolExecutor('cli:bridge-safe', 'safe', workspace=tmp_path)
    result = executor.run('execute_code', {'code': "from zeline_tools import write_file; write_file('no.txt','no')"})
    assert 'not allowed' in result
    assert not (tmp_path / 'no.txt').exists()


def test_tcp_fallback_without_unix_socket_support(executor, monkeypatch):
    from zeline import code_tools
    monkeypatch.delattr(code_tools.socket, 'AF_UNIX', raising=False)
    result=executor.run('execute_code', {'code': "from zeline_tools import write_file,read_file; assert write_file('tcp.txt','real TCP dispatch')['success']; print(read_file('tcp.txt')['output'])"})
    assert result.startswith('exit=0\n') and 'real TCP dispatch' in result, result
    assert (executor.workspace/'tcp.txt').read_text()=='real TCP dispatch'
    assert not any(t.name=='zeline-code-tools' for t in threading.enumerate())


@pytest.mark.parametrize('tcp',[False,True])
def test_invalid_bridge_token_never_dispatches(executor,monkeypatch,tcp):
    from zeline import code_tools
    if tcp:
        monkeypatch.delattr(code_tools.socket,'AF_UNIX',raising=False)
    events=[]
    executor._nested_tool_callback=lambda *args: events.append(args)
    result=executor.run('execute_code',{'code': "import zeline_tools; zeline_tools._bridge_token='invalid'; print(zeline_tools.write_file('denied.txt','must not exist'))"})
    assert 'bridge authentication failed' in result,result
    assert not (executor.workspace/'denied.txt').exists()
    assert events==[]


def test_context_policy_and_no_escalation(executor, monkeypatch):
    events, seen = [], []
    executor._nested_tool_callback = lambda *a: events.append(a)
    original = executor.execution_policy.check
    def check(name, args):
        seen.append((name, executor._control('_task_id')))
        return 'test denial' if name == 'write_file' else original(name, args)
    monkeypatch.setattr(executor.execution_policy, 'check', check)
    context = executor.capture_context()
    context['_task_id'] = 'captured-task'
    result = executor.run('execute_code', {'code': "from zeline_tools import write_file; print(write_file('no.txt','no'))"}, _context=context)
    assert 'test denial' in result
    assert ('write_file', 'captured-task') in seen
    assert not events


@pytest.mark.parametrize('cancel', [False, True])
def test_timeout_cancel_and_cleanup(executor, monkeypatch, cancel):
    from zeline import code_tools
    paths = []
    original = code_tools.tempfile.TemporaryDirectory
    def temporary(*a, **kw):
        obj = original(*a, **kw)
        paths.append(obj.name)
        return obj
    monkeypatch.setattr(code_tools.tempfile, 'TemporaryDirectory', temporary)
    # Give the nested command its own deadline before the owning snippet expires.
    # Equal deadlines race timeout reporting against bridge cancellation.
    monkeypatch.setattr(tools, '_clamp_timeout', lambda value: int(value))
    stop = threading.Event()
    executor._should_stop = stop.is_set
    timer = threading.Timer(.3, stop.set) if cancel else None
    if timer:
        timer.start()
    started = time.monotonic()
    try:
        result = executor.run('execute_code', {'code': f"from zeline_tools import terminal; print(terminal({python_command('import time; time.sleep(20)')!r}, timeout=1))", 'timeout': 3})
    finally:
        if timer:
            timer.cancel()
    assert time.monotonic() - started < 4
    assert ('timed out' in result) if not cancel else ('exit=' in result or 'CANCELLED' in result)
    from pathlib import Path
    assert paths and all(not Path(p).exists() for p in paths)
    assert not any(t.name.startswith('zeline-code-tools') for t in threading.enumerate())
    assert not tools._FG_PROCS.get(executor.identity)


def test_module_not_globally_importable():
    import importlib.util
    assert importlib.util.find_spec('zeline_tools') is None


def test_no_arbitrary_or_recursive_dispatch(executor):
    result = executor.run('execute_code', {'code': '''
import zeline_tools
print(hasattr(zeline_tools, 'execute_code'))
print(zeline_tools._call('execute_code', {'code': "print('BAD')"}))
'''})
    assert 'False' in result and 'not permitted' in result
    assert 'BAD' not in result


def test_real_audit_and_checkpoint_paths(executor, monkeypatch):
    rows, snapshots = [], []
    monkeypatch.setattr(tools.events_module, 'log_tool_call', lambda *a, **kw: rows.append((a, kw)))
    monkeypatch.setattr(tools.checkpoints, 'snapshot', lambda *a, **kw: snapshots.append((a, kw)))
    result = executor.run('execute_code', {'code': "from zeline_tools import write_file, patch; write_file('audit.txt', 'before'); print(patch('audit.txt','before','after'))"})
    assert result.startswith('exit=0'), result
    assert snapshots
    assert rows
    assert 'write_file' in str(rows) and 'patch_file' in str(rows)


def test_unresolved_context_blocks_nested_work(executor):
    context = executor.capture_context()
    context['_task_unresolved'] = True
    result = executor.run('execute_code', {'code': "from zeline_tools import write_file; write_file('no.txt','no')"}, _context=context)
    assert 'unresolved' in result
    assert not (executor.workspace / 'no.txt').exists()


def test_callback_capture_is_stable(executor):
    events = []
    executor._nested_tool_callback = lambda n, a: events.append(n)
    context = executor.capture_context()
    executor._nested_tool_callback = lambda *a: pytest.fail('wrong context callback')
    shell_command = python_command("print('captured')")
    result = executor.run('execute_code', {'code': f"from zeline_tools import terminal; print(terminal(command={shell_command!r}))"}, _context=context)
    assert 'captured' in result and result.startswith('exit=0')
    assert events == ['run_shell']


def test_same_live_run_and_identity(executor, monkeypatch):
    calls = []
    original = executor.run
    def observed(name, args, **kwargs):
        calls.append((executor.identity, executor.profile, executor.workspace, name))
        return original(name, args, **kwargs)
    monkeypatch.setattr(executor, 'run', observed)
    shell_command = python_command("print('live')")
    result = executor.run('execute_code', {'code': f"from zeline_tools import terminal; print(terminal({shell_command!r}))"})
    assert 'live' in result
    assert [call[-1] for call in calls] == ['execute_code', 'run_shell']
    assert all(call[:3] == (executor.identity, 'full', executor.workspace) for call in calls)


def test_plugin_denial_does_not_emit_dispatch_callback(executor, monkeypatch):
    from types import SimpleNamespace
    seen = []
    executor._nested_tool_callback = lambda *a: seen.append(a)
    executor.plugins = SimpleNamespace(
        before=lambda name, args: SimpleNamespace(blocked=name == 'write_file', args=args),
        after=lambda name, args, result: result,
    )
    monkeypatch.setattr(tools.plugin_bus, 'denial_message', lambda *a: 'ERROR: blocked by test plugin')
    result = executor.run('execute_code', {'code': "from zeline_tools import write_file; print(write_file('no.txt','no'))"})
    assert 'blocked by test plugin' in result
    assert not seen
    assert not (executor.workspace / 'no.txt').exists()
