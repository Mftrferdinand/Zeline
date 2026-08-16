#!/usr/bin/env bash
# Offline test suite for the top-level tools (Client Revenue Engine etc.).
# Zero external deps — pure stdlib unittest.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m unittest discover -p 'test_*.py' "$@"
