"""Child resource ceilings stay bounded across POSIX kernel differences."""
import ast
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'zeline/skills/zeline-sat-solvers/scripts/portfolio.py'


DARWIN_BASELINE = 400 * 1024 ** 3


def apply_limits(platform, inherited, baseline=lambda: DARWIN_BASELINE):
    calls = []
    resource = SimpleNamespace(RLIMIT_FSIZE=1, RLIMIT_DATA=2, RLIMIT_CORE=3,
                               RLIM_INFINITY=-1, getrlimit=lambda kind: inherited.get(kind, (-1, -1)),
                               setrlimit=lambda kind, bounds: calls.append((kind, bounds)))
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'child_limits')
    namespace = {'sys': SimpleNamespace(platform=platform), 'MAX_OUTPUT_BYTES': 16 * 1024 ** 2,
                 '_darwin_virtual_size': baseline}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    with patch.dict(sys.modules, {'resource': resource}):
        namespace['child_limits']()
    return dict(calls)


def test_darwin_retains_baseline_relative_memory_guard_in_fresh_interpreter():
    limits = apply_limits('darwin', {})
    assert limits[2] == (DARWIN_BASELINE + 2 * 1024 ** 3,) * 2
    assert limits[2][0] - DARWIN_BASELINE == 2 * 1024 ** 3
    assert limits[1] == (16 * 1024 ** 2,) * 2
    assert limits[3] == (0, 0)


def test_inherited_soft_and_hard_ceilings_are_never_raised():
    limits = apply_limits('linux', {1: (4096, 8192), 2: (-1, 128 * 1024 ** 2)})
    assert limits[1] == (4096, 4096)
    assert limits[2] == (128 * 1024 ** 2,) * 2


def test_linux_unlimited_parent_retains_existing_child_ceiling():
    assert apply_limits('linux', {})[2] == (2 * 1024 ** 3,) * 2


@pytest.mark.parametrize('soft,hard', [(DARWIN_BASELINE + 1024, -1),
                                      (-1, DARWIN_BASELINE + 2048), (4096, 8192)])
def test_darwin_never_raises_inherited_memory_ceilings(soft, hard):
    expected = min(v for v in (soft, hard, DARWIN_BASELINE + 2 * 1024 ** 3) if v != -1)
    assert apply_limits('darwin', {2: (soft, hard)})[2] == (expected, expected)


def test_darwin_probe_failure_fails_closed():
    def denied():
        raise OSError('libproc unavailable')
    with pytest.raises(OSError, match='libproc unavailable'):
        apply_limits('darwin', {}, denied)


def test_linux_does_not_probe_darwin():
    def forbidden():
        pytest.fail('Linux must not invoke Darwin libproc')
    assert apply_limits('linux', {}, forbidden)[2] == (2 * 1024 ** 3,) * 2


def darwin_probe():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_darwin_virtual_size')
    namespace = {'os': os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['_darwin_virtual_size']


@pytest.mark.parametrize('received,virtual', [(96, DARWIN_BASELINE), (0, DARWIN_BASELINE),
                                             (95, DARWIN_BASELINE), (96, 0)])
def test_libproc_baseline_probe_validates_exact_abi_result(received, virtual):
    import ctypes
    from unittest.mock import Mock
    def pidinfo(pid, flavor, arg, info, size):
        assert (pid, flavor, arg, size) == (os.getpid(), 4, 0, 96)
        info._obj.pti_virtual_size = virtual
        return received
    library = SimpleNamespace(proc_pidinfo=Mock(side_effect=pidinfo))
    with patch.object(ctypes, 'CDLL', return_value=library) as load:
        if received == 96 and virtual:
            assert darwin_probe()() == virtual
        else:
            with pytest.raises(OSError, match='cannot measure Darwin SAT'):
                darwin_probe()()
    load.assert_called_once_with('/usr/lib/libproc.dylib', use_errno=True)
    assert library.proc_pidinfo.restype is ctypes.c_int
    assert library.proc_pidinfo.argtypes == [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                            ctypes.c_void_p, ctypes.c_int]


def test_libproc_load_failure_is_not_suppressed():
    import ctypes
    with patch.object(ctypes, 'CDLL', side_effect=OSError('library denied')):
        with pytest.raises(OSError, match='library denied'):
            darwin_probe()()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX exec/resource-limit acceptance')
def test_darwin_wrapper_executes_real_child_with_memory_limit(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    spec = importlib.util.spec_from_file_location('portability_portfolio', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executable = tmp_path / 'fixture-solver'
    baseline_file = tmp_path / 'baseline'
    executable.write_text('#!' + sys.executable + '\nimport resource\nfrom pathlib import Path\n'
                          f'baseline = int(Path({str(baseline_file)!r}).read_text())\n'
                          'soft, hard = resource.getrlimit(resource.RLIMIT_DATA)\n'
                          'assert soft == hard and 0 < soft <= baseline + 2 * 1024 ** 3\n'
                          + ('assert soft > baseline\n' if sys.platform == 'darwin' else '')
                          + "print('s SATISFIABLE')\nprint('v 1 0')\n", encoding='utf-8')
    # Native Darwin CI is required to validate the real XNU limit/exec behavior.
    native_darwin = sys.platform == 'darwin'
    executable.chmod(0o700)
    spawn = module.subprocess.Popen
    launches = []
    def launch(argv, **kwargs):
        launches.append((argv, kwargs.get('preexec_fn')))
        # Record the exact pre-limit baseline in the fresh child, not its parent.
        # Linux exercises exec/cleanup with a unit-only probe, never Darwin libproc.
        probe = 'probe()' if native_darwin else '0'
        setup = ('import portfolio; probe=portfolio._darwin_virtual_size; '
                 'sys.platform="darwin"; '
                 'portfolio._darwin_virtual_size=lambda: '
                 f'(open({str(baseline_file)!r}, "w").write(str(b:={probe})), b)[1]; ')
        argv = [*argv[:2], argv[2].replace('from portfolio import child_limits;',
                                         setup + 'from portfolio import child_limits;'), *argv[3:]]
        return spawn(argv, **kwargs)
    with patch.object(module.sys, 'platform', 'darwin'), \
         patch.object(module.subprocess, 'Popen', side_effect=launch):
        result = module.run_backend(SimpleNamespace(num_vars=1, clauses=[[1]], original=[[1]]),
                                    'yalsat', str(executable), 5)
    assert result['status'] == 'SAT', result
    assert launches[0][0][:2] == [sys.executable, '-c']
    assert launches[0][1] is None
