"""Nonce v3 CLI compatibility, delegating to the independently verified native portfolio.

The original uploaded algorithm remains unchanged under assets/supplied/.
This entrypoint preserves its bitstring interface, not its historical benchmark claims.
"""
import argparse
import json
import sys

from portfolio import SOLVERS, solve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance')
    parser.add_argument('timeout', nargs='?', type=float, default=30)
    parser.add_argument('--out')
    parser.add_argument('--vars', type=int, dest='num_vars')
    parser.add_argument('--solver', choices=('auto',) + SOLVERS, default='auto')
    args = parser.parse_args()
    answer = solve(args.instance, solver=args.solver, timeout=args.timeout,
                   num_vars=args.num_vars, out=args.out)
    print(json.dumps({k: v for k, v in answer.items()
                      if k not in {'assignment', 'attempts'}}, sort_keys=True), file=sys.stderr)
    if answer['status'] == 'SAT' and answer['verified']:
        print(answer['assignment'])
        return 0
    return 20 if answer['status'] == 'UNSAT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
