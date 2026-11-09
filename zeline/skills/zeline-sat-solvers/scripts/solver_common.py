"""Strict, dependency-free CNF parsing and independent model verification."""
from dataclasses import dataclass
import json
import os
import stat

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_VARS = 1_000_000
MAX_CLAUSES = 1_000_000
MAX_LITERALS = 5_000_000


@dataclass
class Instance:
    num_vars: int
    original: list
    clauses: list


def dimension(n):
    if type(n) is not int or not 0 <= n <= MAX_VARS:
        raise ValueError('invalid variable dimension or operational limit exceeded')
    return n


def load_instance(path, num_vars=None):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('CNF input must be a regular file')
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('input exceeds 32 MiB operational limit')
    text = data.decode('utf-8')
    declared = None
    if text.lstrip().startswith(('[', '{')):
        obj = json.loads(text)
        if isinstance(obj, dict):
            # Native SMT/XOR support must not turn a partial CNF witness into a verified full challenge.
            if any(key in obj for key in ('xor', 'xors', 'xor_clauses', 'smt2', 'assertions', 'constraints')):
                raise ValueError('unsupported non-CNF constraints; export every constraint to CNF')
            if 'clauses' not in obj:
                raise ValueError('missing clauses')
            clauses = obj['clauses']
            if 'num_vars' in obj:
                declared = dimension(obj['num_vars'])
        else:
            clauses = obj
    else:
        clauses, pending, header = [], [], None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('c'):
                continue
            fields = line.split()
            if fields[0] == 'p':
                if header is not None or len(fields) != 4 or fields[1] != 'cnf':
                    raise ValueError('invalid DIMACS header')
                if not all(x.isascii() and x.isdigit() for x in fields[2:]):
                    raise ValueError('invalid DIMACS dimensions')
                declared = dimension(int(fields[2]))
                count = int(fields[3])
                if count > MAX_CLAUSES:
                    raise ValueError('clause limit exceeded')
                header = True
            else:
                if header is None:
                    raise ValueError('missing DIMACS header')
                for word in fields:
                    if not word.lstrip('-').isascii() or not word.lstrip('-').isdigit() or word.startswith('--'):
                        raise ValueError('invalid DIMACS literal')
                    literal = int(word)
                    if literal == 0:
                        clauses.append(pending)
                        pending = []
                    else:
                        pending.append(literal)
        if header is None or pending or len(clauses) != count:
            raise ValueError('DIMACS clause count or terminator mismatch')
    if not isinstance(clauses, list) or len(clauses) > MAX_CLAUSES:
        raise ValueError('invalid clauses or clause limit exceeded')
    maximum = total = 0
    normalized = []
    for clause in clauses:
        if not isinstance(clause, list):
            raise ValueError('clause must be a list')
        total += len(clause)
        if total > MAX_LITERALS:
            raise ValueError('literal limit exceeded')
        for literal in clause:
            if type(literal) is not int or literal == 0 or abs(literal) > MAX_VARS:
                raise ValueError('invalid literal')
            maximum = max(maximum, abs(literal))
        unique = set(clause)
        if not any(-literal in unique for literal in unique):
            normalized.append(list(dict.fromkeys(clause)))
    if num_vars is not None:
        dimension(num_vars)
        if declared is not None and num_vars != declared:
            raise ValueError('--vars conflicts with declared dimension')
        declared = num_vars
    n = maximum if declared is None else declared
    if maximum > n:
        raise ValueError('literal outside declared dimension')
    return Instance(n, clauses, normalized)


def verify(instance, bits):
    return (isinstance(bits, str) and len(bits) == instance.num_vars
            and set(bits) <= {'0', '1'}
            and all(any((bits[abs(v) - 1] == '1') == (v > 0) for v in clause)
                    for clause in instance.original))
