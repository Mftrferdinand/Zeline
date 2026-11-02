"""Offline external SAT solver portfolio. No Python solver dependencies."""
import argparse
import json
import math
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any

from solver_common import load_instance, verify

SOLVERS = ('z3', 'kissat', 'cadical', 'glucose', 'minisat', 'yalsat', 'probsat',
           'probsat_official', 'cryptominisat', 'bitwuzla')
LOCAL = {'yalsat', 'probsat', 'probsat_official'}
BINARY_NAMES = {'probsat': ('probsab',), 'probsat_official': ('probSAT', 'probsat'),
                'cryptominisat': ('cryptominisat5', 'cryptominisat')}
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def binary(name):
    names = BINARY_NAMES.get(name, (name,))
    directories = []
    override = os.environ.get('ZELINE_SAT_BIN_DIR')
    if override:
        directories.append(Path(override).expanduser())
    directories.append(Path.home() / '.local/share/zeline/solvers/bin')
    for directory in directories:
        for executable in names:
            path = directory / executable
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
    return next((path for executable in names if (path := shutil.which(executable))), None)


def inspect():
    return {name: {'available': bool(path), 'path': path, 'complete': name not in LOCAL}
            for name in SOLVERS for path in [binary(name)]}


def result(status: str, solver: str, **extra: Any) -> dict[str, Any]:
    return dict(status=status, solver=solver, verified=False, **extra)


def parse_output(text, instance, solver, returncode):
    if solver == 'bitwuzla':
        labels = [line.strip() for line in text.splitlines()
                  if line.strip() in {'sat', 'unsat', 'unknown'}]
        if len(labels) != 1:
            return result('ERROR', solver, error='missing or conflicting SMT status')
        definitions = re.findall(
            r'\(define-fun\s+v(\d+)\s+\(\s*\)\s+Bool\s+(true|false)\s*\)', text)
        if labels[0] == 'sat' and instance.num_vars and not definitions:
            return result('ERROR', solver, error='missing Boolean SMT model')
        header = {'sat': 's SATISFIABLE', 'unsat': 's UNSATISFIABLE', 'unknown': ''}[labels[0]]
        text = header + '\n' + ''.join(
            'v ' + ('' if value == 'true' else '-') + var + ' 0\n'
            for var, value in definitions)
    if solver == 'probsat':
        if returncode == 1:
            return result('UNKNOWN', solver)
        if returncode != 0:
            return result('ERROR', solver, error='solver exit code ' + str(returncode))
        models = [line.strip() for line in text.splitlines()
                  if len(line.strip()) == instance.num_vars and set(line.strip()) <= {'0', '1'}]
        if len(models) == 1 and verify(instance, models[0]):
            answer = result('SAT', solver, assignment=models[0])
            answer['verified'] = True
            return answer
        return result('ERROR', solver, error='missing or invalid custom bitstring model')
    statuses = set()
    values = {}
    for line in text.splitlines():
        words = line.strip().split()
        if not words:
            continue
        if words in (['s', 'UNSATISFIABLE'], ['UNSAT']):
            statuses.add('UNSAT')
        elif words in (['s', 'SATISFIABLE'], ['SAT']):
            statuses.add('SAT')
        elif words[0] == 'v' or (solver in {'minisat', 'glucose'} and all(w.lstrip('-').isdigit() for w in words)):
            for word in words[1:] if words[0] == 'v' else words:
                try:
                    n = int(word)
                except ValueError:
                    return result('ERROR', solver, error='malformed model')
                if n == 0:
                    continue
                if abs(n) > instance.num_vars or (abs(n) in values and values[abs(n)] != (n > 0)):
                    return result('ERROR', solver, error='inconsistent model')
                values[abs(n)] = n > 0
    if len(statuses) > 1:
        return result('ERROR', solver, error='conflicting statuses')
    if returncode not in (0, 10, 20):
        return result('ERROR', solver, error='solver exit code ' + str(returncode))
    if 'UNSAT' in statuses:
        if returncode == 10:
            return result('ERROR', solver, error='status/exit mismatch')
        return result('UNKNOWN' if solver in LOCAL else 'UNSAT', solver)
    if 'SAT' in statuses:
        if returncode == 20:
            return result('ERROR', solver, error='status/exit mismatch')
        bits = ''.join('1' if values.get(i, False) else '0' for i in range(1, instance.num_vars + 1))
        if verify(instance, bits):
            out = result('SAT', solver, assignment=bits)
            out['verified'] = True
            return out
        return result('ERROR', solver, error='model fails independent original-clause verification')
    return result('UNKNOWN', solver)


def child_limits():
    """POSIX child-only limits; never change the agent process's resources."""
    import resource
    for label, kind, bound in (('FSIZE', resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES),
                               ('DATA', resource.RLIMIT_DATA, 2 * 1024 ** 3),
                               ('CORE', resource.RLIMIT_CORE, 0)):
        try:
            _, hard = resource.getrlimit(kind)
            value = bound if hard == resource.RLIM_INFINITY else min(bound, hard)
            resource.setrlimit(kind, (value, value))
        except (OSError, ValueError) as exc:
            # Expose only a safe limit identifier/type/errno, never process data.
            os.write(2, ('ZELINE_LIMIT_SETUP ' + label + ' ' + type(exc).__name__
                         + ' errno=' + str(getattr(exc, 'errno', None)) + '\n').encode())
            raise


def run_backend(instance, solver, executable, timeout):
    with tempfile.TemporaryDirectory(prefix='zeline-sat-') as directory:
        root = Path(directory)
        source = root / 'input.cnf'
        source.write_text('p cnf %d %d\n' % (instance.num_vars, len(instance.clauses)) + ''.join(' '.join(map(str, c)) + ' 0\n' for c in instance.clauses))
        argv = [executable, '-dimacs', str(source)] if solver == 'z3' else [executable, str(source)]
        if solver == 'probsat_official':
            argv = [executable, '-a', str(source), '1']
        elif solver == 'cryptominisat':
            argv = [executable, '--threads=1', '--verb=0', str(source)]
        if solver == 'bitwuzla':
            source = root / 'input.smt2'
            declarations = ''.join(f'(declare-fun v{i} () Bool)\n' for i in range(1, instance.num_vars + 1))
            assertions = []
            for clause in instance.clauses:
                terms = [f'v{lit}' if lit > 0 else f'(not v{-lit})' for lit in clause]
                expression = ('false' if not terms else terms[0] if len(terms) == 1
                              else '(or ' + ' '.join(terms) + ')')
                assertions.append('(assert ' + expression + ')\n')
            source.write_text('(set-logic QF_BV)\n' + declarations + ''.join(assertions) + '(check-sat)\n')
            argv = [executable, '--print-model', str(source)]
        model_file = root / 'model'
        if solver in {'minisat', 'glucose'}:
            argv.append(str(model_file))
        if solver == 'probsat':
            if any(len(c) > 16 for c in instance.clauses):
                return result('ERROR', solver, error='custom probsab supports clause width at most 16')
            if any(not c for c in instance.clauses):
                return result('UNKNOWN', solver, error='empty clause cannot be solved by local search')
            if not instance.clauses:
                bits = '0' * instance.num_vars
                if verify(instance, bits):
                    answer = result('SAT', solver, assignment=bits)
                    answer['verified'] = True
                    return answer
            source.write_text('%d %d\n' % (instance.num_vars, len(instance.clauses)) + ''.join(' '.join(map(str, c)) + ' 0\n' for c in instance.clauses))
            argv.extend(['1', '2.3', str(timeout)])
        output = root / 'stdout'
        with output.open('wb') as stream:
            try:
                process = subprocess.Popen(argv, stdout=stream, stderr=stream, shell=False,
                                           start_new_session=(os.name == 'posix'),
                                           preexec_fn=child_limits if os.name == 'posix' else None)
            except (OSError, subprocess.SubprocessError) as exc:
                # Popen has already reaped failed pre-exec children. Retain only
                # our fixed diagnostic marker, not arbitrary stderr or paths.
                stream.flush()
                marker = re.search(r'ZELINE_LIMIT_SETUP [A-Z]+ [A-Za-z]+ errno=(?:None|[0-9]+)',
                                   output.read_text(errors='replace')[:1024])
                detail = '; ' + marker.group(0) if marker else ''
                return result('ERROR', solver, error='solver setup failed: ' + type(exc).__name__ + detail)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if output.stat().st_size > MAX_OUTPUT_BYTES:
                        return result('ERROR', solver, error='output limit exceeded')
                    if time.monotonic() >= deadline:
                        return result('TIMEOUT', solver)
                    time.sleep(min(.01, max(0, deadline - time.monotonic())))
            finally:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    process.kill()
                process.wait()
        if output.stat().st_size > MAX_OUTPUT_BYTES:
            return result('ERROR', solver, error='output limit exceeded')
        text = output.read_text(errors='replace')
        if model_file.exists():
            if model_file.stat().st_size > MAX_OUTPUT_BYTES:
                return result('ERROR', solver, error='model output limit exceeded')
            # Some Glucose builds put SAT status on stdout and only literals in the file.
            # Model literals remain authoritative in the fresh file; retain stdout status only.
            status_lines = [line for line in text.splitlines() if line.strip() in
                            {'SAT', 'UNSAT', 's SATISFIABLE', 's UNSATISFIABLE'}]
            text = '\n'.join(status_lines) + '\n' + model_file.read_text(errors='replace')
        return parse_output(text, instance, solver, process.returncode)


def solve(instance, solver='auto', timeout=10, num_vars=None, out=None):
    try:
        if solver not in SOLVERS + ('auto',):
            raise ValueError('unknown solver')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        deadline = time.monotonic() + timeout
        formula = load_instance(instance, num_vars)
        if time.monotonic() >= deadline:
            return result('TIMEOUT', solver)
        if any(not clause for clause in formula.clauses):
            answer = result('UNSAT', 'preprocess', reason='empty clause contradiction')
        elif not formula.clauses:
            bits = '0' * formula.num_vars
            if not verify(formula, bits):
                return result('ERROR', 'preprocess', error='invalid normalized formula')
            answer = result('SAT', 'preprocess', assignment=bits)
            answer['verified'] = True
        else:
            selected = SOLVERS if solver == 'auto' else (solver,)
            attempts = []
            available = [(name, binary(name)) for name in selected]
            for index, (name, executable) in enumerate(available):
                if not executable:
                    attempts.append(result('UNAVAILABLE', name))
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    answer = result('TIMEOUT', name)
                    break
                count = sum(bool(path) for _, path in available[index:])
                allocation = remaining / max(1, count) if solver == 'auto' else remaining
                answer = run_backend(formula, name, executable, allocation)
                if time.monotonic() >= deadline:
                    answer = result('TIMEOUT', name)
                attempts.append(answer)
                if answer['status'] in ('SAT', 'UNSAT'):
                    break
            else:
                if len(selected) == 1:
                    answer = attempts[-1]
                else:
                    status = ('TIMEOUT' if any(a['status'] == 'TIMEOUT' for a in attempts) else
                              'UNKNOWN' if any(a['status'] != 'UNAVAILABLE' for a in attempts) else
                              'UNAVAILABLE')
                    answer = result(status, 'auto')
            if solver == 'auto':
                answer = dict(answer, attempts=attempts)
        if out is not None:
            destination = Path(out)
            if destination.resolve() == Path(instance).resolve() or destination.exists() or destination.is_symlink():
                raise ValueError('output must be new and must not overwrite input')
            if answer['status'] != 'SAT' or not answer['verified']:
                return answer
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                try:
                    stream.write(answer['assignment'] + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
        return answer
    except (ValueError, OSError, UnicodeError, RecursionError) as exc:
        return result('ERROR', solver, error=str(exc))


def main(forced_solver=None):
    parser = argparse.ArgumentParser(description=__doc__)
    if forced_solver is None:
        sub = parser.add_subparsers(dest='command', required=True)
        sub.add_parser('inspect')
        command = sub.add_parser('solve')
    else:
        command = parser
    command.add_argument('instance')
    if forced_solver is None:
        command.add_argument('--solver', choices=('auto',) + SOLVERS, default='auto')
    command.add_argument('--timeout', type=float, default=10)
    command.add_argument('--vars', type=int, dest='num_vars')
    command.add_argument('--out')
    args = parser.parse_args()
    if forced_solver is None and args.command == 'inspect':
        print(json.dumps(inspect(), sort_keys=True))
    else:
        answer = solve(args.instance, solver=forced_solver or args.solver, timeout=args.timeout,
                       num_vars=args.num_vars, out=args.out)
        print(json.dumps(answer, sort_keys=True))
        if answer['status'] not in {'SAT', 'UNSAT'}:
            raise SystemExit(2)


if __name__ == '__main__':
    main()
