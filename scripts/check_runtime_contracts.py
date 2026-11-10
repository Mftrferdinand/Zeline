#!/usr/bin/env python3
"""Check cross-module runtime compatibility without touching operator state.

Run with the actual installed interpreter, from outside the source checkout:
  ~/.local/share/zeline/venv/bin/python scripts/check_runtime_contracts.py
Use --source-root /path/to/checkout to check that tree instead.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    probe = '''
import inspect,json
from zeline.agent import Zeline
from zeline.sessions import SessionStore
from zeline.session_store import SessionPersistence
from zeline.gateways import telegram
from zeline import archive_intake, task_context
checks = {
    'storage_recovery_present': callable(getattr(SessionPersistence, 'recovery_present', None)),
    'storage_save_recovery': callable(getattr(SessionPersistence, 'save_recovery', None)),
    'archive_detail': callable(getattr(SessionPersistence, 'archive_detail', None)),
    'task_store': callable(getattr(task_context, 'TaskContextStore', None)),
    'typing_heartbeat': callable(getattr(telegram, '_start_working_heartbeat', None)),
    'document_prepare': callable(getattr(telegram, '_prepare_document_prompt', None)),
    'agent_task_reference': 'task_reference' in inspect.signature(Zeline.send).parameters,
    'session_task_reference': 'task_reference' in inspect.signature(SessionStore.send).parameters,
    'gateway_task_reference': 'task_reference' in inspect.signature(telegram._send_agent_reply).parameters,
}
assert all(checks.values()), checks
store = SessionStore()
session = store.get_or_create('offline:runtime-contract', 'safe')
checks['session_create'] = session is not None
print(json.dumps({'ok': all(checks.values()), 'checks': checks,
                  'package': inspect.getfile(Zeline), 'production_state_touched': False}))
'''
    with tempfile.TemporaryDirectory(prefix='zeline-runtime-contract-') as temporary:
        env = os.environ.copy()
        env.update(ZELINE_HOME=temporary, ZELINE_API_KEY='inert-fixture',
                   ZELINE_MODEL='inert-fixture', ZELINE_BASE_URL='http://127.0.0.1:1/v1')
        # Explicit source selection only; the default must check the installation.
        env.pop('PYTHONPATH', None)
        if args.source_root:
            env['PYTHONPATH'] = str(args.source_root.resolve())
        result = subprocess.run([sys.executable, '-c', probe], env=env,
                                cwd=temporary, capture_output=True, text=True,
                                timeout=45)
        print(result.stdout.strip())
        if result.returncode:
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
