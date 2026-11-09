"""Zeline AgentID Watcher offline integration; never imports or starts the pinned engine."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

SKILL = Path(__file__).resolve().parents[1]
UPSTREAM = SKILL / 'assets' / 'upstream'


def inspect_bundle() -> dict:
    manifest = json.loads((SKILL / 'assets' / 'provenance.json').read_text())
    files = manifest['files']
    if not isinstance(files, dict) or len(files) != 12:
        raise ValueError('Invalid upstream manifest')
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('Unsafe manifest path')
        path = UPSTREAM / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
            raise ValueError('Missing or unsafe upstream file')
        if not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise ValueError('Invalid manifest digest')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Upstream file integrity mismatch: ' + name)
    version = json.loads((UPSTREAM / 'version.json').read_text())
    if version['sha256'] != files['watch.mjs']:
        raise ValueError('Upstream watch digest disagrees with version metadata')
    if version['version'] != manifest['upstream_version']:
        raise ValueError('Upstream version mismatch')
    return {
        'skill': 'zeline-agentid-watcher', 'upstream_version': version['version'],
        'upstream_files': len(files), 'integrity_ok': True,
        'engine_started': False, 'wallet_session_checked': False,
        'live_trading_enabled': False, 'network_requests': 0,
    }


def prepare_workspace(workspace: str) -> dict:
    result = inspect_bundle()
    requested = Path(workspace).expanduser()
    if requested.is_symlink() or requested.exists():
        raise ValueError('Workspace already exists')
    parent = requested.parent.resolve(strict=True)
    target = parent / requested.name
    if target.is_relative_to(SKILL):
        raise ValueError('Workspace must be outside the installed skill')
    for ancestor in SKILL.parents:
        if (ancestor / '.git').exists() and target.is_relative_to(ancestor):
            raise ValueError('Workspace must be outside the source repository')
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    files = {
        'config.example.json': (UPSTREAM / 'config.example.json').read_bytes(),
        'watch.upstream.mjs': (UPSTREAM / 'watch.mjs').read_bytes(),
        'LICENSE': (UPSTREAM / 'LICENSE').read_bytes(),
        'version.json': (UPSTREAM / 'version.json').read_bytes(),
        'watch.mjs': (
            "console.error('Zeline: live strategy is not enabled. Use the Binance wallet skill "
            "with explicit per-transaction approval; this entrypoint never loads the upstream engine.');\n"
            "process.exit(78);\n"
        ).encode(),
        'README.md': (SKILL / 'references' / 'integration.md').read_bytes(),
        '.gitignore': b'config.json\ndata/\n.baw/\nbaw2/\n*.log\n.env\n',
    }
    for name, data in files.items():
        path = target / name
        with path.open('xb') as output:
            path.chmod(0o600)
            output.write(data)
    result.update({'workspace_prepared': True, 'files_created': len(files)})
    return result


def check_dependencies() -> dict:
    result = inspect_bundle()
    commands = {name: shutil.which(name) for name in ('node', 'baw')}
    missing = [name for name, path in commands.items() if not path]
    checks = []
    for name, executable in commands.items():
        if not executable:
            continue
        try:
            run = subprocess.run([executable, '--version'], capture_output=True, text=True,
                                 timeout=5, shell=False)
            match = re.search(r'\b(?:v)?(\d+)\.(\d+)\.(\d+)\b', run.stdout[:4096])
            version = tuple(map(int, match.groups())) if match else ()
            minimum = (20, 0, 0) if name == 'node' else (1, 12, 0)
            checks.append({'command': name + ' --version',
                           'passed': run.returncode == 0 and bool(version) and version >= minimum,
                           'version': '.'.join(map(str, version)) or None})
        except (OSError, subprocess.TimeoutExpired):
            checks.append({'command': name + ' --version', 'passed': False})
    if commands['node']:
        for name in ('watch.mjs', 'login.mjs', 'setup.mjs'):
            try:
                run = subprocess.run([commands['node'], '--check', str(UPSTREAM / name)],
                                     capture_output=True, text=True, timeout=5, shell=False)
                checks.append({'command': 'node --check ' + name, 'passed': run.returncode == 0})
            except (OSError, subprocess.TimeoutExpired):
                checks.append({'command': 'node --check ' + name, 'passed': False})
    result.update({'dependencies_ready': not missing and all(c['passed'] for c in checks),
                   'missing_commands': missing, 'checks': checks})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'check', 'prepare'])
    parser.add_argument('--workspace')
    args = parser.parse_args()
    if args.action == 'prepare' and not args.workspace:
        parser.error('prepare requires --workspace pointing to a new private directory')
    try:
        if args.action == 'prepare':
            result = prepare_workspace(args.workspace)
        elif args.action == 'check':
            result = check_dependencies()
        else:
            result = inspect_bundle()
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'error': 'Bundle validation or private workspace preparation failed', 'engine_started': False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get('dependencies_ready', True) else 1


if __name__ == '__main__':
    sys.exit(main())
