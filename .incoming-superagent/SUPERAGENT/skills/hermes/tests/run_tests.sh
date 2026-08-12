#!/usr/bin/env bash
# Run the Hermes crypto script test suite.
#
# Zero external dependencies: the suite stubs heavy 3rd-party packages
# (web3, eth_account, httpx, solana, ...) via _bootstrap.py, so it runs
# offline with just the Python standard library.
#
# Usage:
#   ./run_tests.sh            # run everything (stdlib unittest)
#   ./run_tests.sh -v         # verbose
#   pytest                    # also works if pytest is installed
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -p 'test_*.py' "$@"
