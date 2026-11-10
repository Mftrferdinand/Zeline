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


def apply_limits(platform, inherited):
    calls = []
    resource = SimpleNamespace(RLIMIT_FSIZE=1, RLIMIT_DATA=2, RLIMIT_CORE=3,
                               RLIM_INFINITY=-1, getrlimit=lambda kind: inherited.get(kind, (-1, -1)),
                               setrlimit=lambda kind, bounds: calls.append((kind, bounds)))
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'child_limits')
    namespace = {'sys': SimpleNamespace(platform=platform), 'MAX_OUTPUT_BYTES': 16 * 1024 ** 2}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    with patch.dict(sys.modules, {'resource': resource}):
        namespace['child_limits']()
    return dict(calls)


def test_darwin_retains_memory_guard_in_fresh_interpreter():
    limits = apply_limits('darwin', {})
    assert limits[2] == (2 * 1024 ** 3,) * 2
    assert limits[1] == (16 * 1024 ** 2,) * 2
    assert limits[3] == (0, 0)


def test_inherited_soft_and_hard_ceilings_are_never_raised():
    limits = apply_limits('linux', {1: (4096, 8192), 2: (-1, 128 * 1024 ** 2)})
    assert limits[1] == (4096, 4096)
    assert limits[2] == (128 * 1024 ** 2,) * 2


def test_linux_unlimited_parent_retains_existing_child_ceiling():
    assert apply_limits('linux', {})[2] == (2 * 1024 ** 3,) * 2


@pytest.mark.skipif(os.name != 'posix', reason='POSIX exec/resource-limit acceptance')
def test_darwin_wrapper_executes_real_child_with_memory_limit(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SOURCE.parent))
    spec = importlib.util.spec_from_file_location('portability_portfolio', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executable = tmp_path / 'fixture-solver'
    executable.write_text('#!' + sys.executable + '\nimport resource\n'
                          'assert resource.getrlimit(resource.RLIMIT_DATA)[0] <= 2 * 1024 ** 3\n'
                          "print('s SATISFIABLE')\nprint('v 1 0')\n", encoding='utf-8')
    executable.chmod(0o700)
    spawn = module.subprocess.Popen
    launches = []
    def launch(argv, **kwargs):
        launches.append((argv, kwargs.get('preexec_fn')))
        return spawn(argv, **kwargs)
    with patch.object(module.sys, 'platform', 'darwin'), \
         patch.object(module.subprocess, 'Popen', side_effect=launch):
        result = module.run_backend(SimpleNamespace(num_vars=1, clauses=[[1]], original=[[1]]),
                                    'yalsat', str(executable), 5)
    assert result['status'] == 'SAT', result
    assert launches[0][0][:2] == [sys.executable, '-c']
    assert launches[0][1] is None
