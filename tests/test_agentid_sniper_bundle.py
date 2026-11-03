"""Offline contract for the bundled Agent ID Sniper integration."""
import ast
import glob
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'zeline' / 'skills' / 'zeline-agentid-watcher'
RUNNER = SKILL / 'scripts' / 'zeline_agentid.py'


class AgentIdSniperBundleTests(unittest.TestCase):
    def test_bundle_preserves_verified_upstream_and_has_discoverable_entrypoint(self):
        self.assertTrue((SKILL / 'SKILL.md').is_file(), 'Agent ID Sniper is not bundled')
        upstream = SKILL / 'assets' / 'upstream'
        manifest = json.loads((SKILL / 'assets' / 'provenance.json').read_text())
        self.assertEqual(len(manifest['files']), 12)
        for name, expected in manifest['files'].items():
            self.assertEqual(hashlib.sha256((upstream / name).read_bytes()).hexdigest(), expected)
        version = json.loads((upstream / 'version.json').read_text())
        self.assertEqual(manifest['files']['watch.mjs'], version['sha256'])
        self.assertIn('MIT License', (upstream / 'LICENSE').read_text())
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, ZELINE_HOME=str(Path(temp) / 'home'))
            code = (
                'from zeline import skills;'
                'skills.seed_skills();'
                "s=skills.load_skill('zeline-agentid-watcher');"
                "assert 'Zeline AgentID Watcher' in s;"
                "assert 'scripts/zeline_agentid.py' in s;"
                "r=skills.load_skill('zeline-agentid-watcher',file_path='references/integration.md');"
                "assert 'No automatic trading' in r"
            )
            result = subprocess.run([sys.executable, '-c', code], env=env, cwd=ROOT,
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_inspection_verifies_files_without_accessing_wallet_or_starting_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, HOME=temp, ZELINE_HOME=str(Path(temp) / 'home'))
            result = subprocess.run([sys.executable, str(RUNNER), 'inspect'], env=env,
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(data['integrity_ok'])
            self.assertEqual(data['upstream_files'], 12)
            self.assertFalse(data['engine_started'])
            self.assertFalse(data['wallet_session_checked'])
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_prepare_creates_private_inactive_workspace_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'workspace'
            command = [sys.executable, str(RUNNER), 'prepare', '--workspace', str(target)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data['engine_started'])
            self.assertFalse(data['live_trading_enabled'])
            self.assertTrue((target / 'watch.upstream.mjs').is_file())
            self.assertFalse((target / 'config.json').exists())
            self.assertNotIn('import ', (target / 'watch.mjs').read_text())
            self.assertIn('process.exit(78)', (target / 'watch.mjs').read_text())
            if os.name != 'nt':
                self.assertEqual(target.stat().st_mode & 0o777, 0o700)
                self.assertEqual((target / 'config.example.json').stat().st_mode & 0o777, 0o600)
            before = {p.name: p.read_bytes() for p in target.iterdir()}
            again = subprocess.run(command, capture_output=True, text=True, timeout=8)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in target.iterdir()})

    def test_check_reports_missing_dependencies_without_starting_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, PATH=temp, HOME=temp)
            result = subprocess.run([sys.executable, str(RUNNER), 'check'], env=env,
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 1, result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data['dependencies_ready'])
            self.assertEqual(data['missing_commands'], ['node', 'baw'])
            self.assertFalse(data['engine_started'])
            self.assertFalse(data['wallet_session_checked'])
            self.assertEqual(data['network_requests'], 0)

    def test_package_data_patterns_include_hidden_upstream_manifest_member(self):
        line = next(line for line in (ROOT / 'pyproject.toml').read_text().splitlines()
                    if line.startswith('zeline = ['))
        patterns = ast.literal_eval(line.split('=', 1)[1].strip())
        packaged = set()
        for pattern in patterns:
            packaged.update(Path(p).resolve() for p in glob.glob(str(ROOT / 'zeline' / pattern), recursive=True))
        self.assertTrue((SKILL / 'assets' / 'upstream' / '.gitignore').resolve() in packaged,
                        'Fresh package would lose the hidden upstream file and fail integrity checks')

    def test_tampered_engine_is_rejected_before_workspace_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'bundle'
            shutil.copytree(SKILL, bundle, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            watch = bundle / 'assets' / 'upstream' / 'watch.mjs'
            watch.write_bytes(watch.read_bytes() + b'\n// tampered test copy\n')
            target = Path(temp) / 'workspace'
            result = subprocess.run([sys.executable, str(bundle / 'scripts' / RUNNER.name),
                                     'prepare', '--workspace', str(target)],
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(target.exists())
            self.assertFalse(json.loads(result.stdout)['engine_started'])

    def test_legacy_name_routes_to_the_canonical_skill_without_duplicating_engine(self):
        legacy = SKILL.parent / 'agentid-sniper'
        self.assertTrue((legacy / 'SKILL.md').is_file())
        self.assertIn('zeline-agentid-watcher', (legacy / 'SKILL.md').read_text())
        self.assertFalse((legacy / 'assets').exists())
        result = subprocess.run([sys.executable, str(legacy / 'scripts' / 'agentid_sniper.py'), 'inspect'],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['skill'], 'zeline-agentid-watcher')

    def test_live_start_is_not_exposed(self):
        result = subprocess.run([sys.executable, str(RUNNER), 'start'],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)

    def test_workspace_inside_package_is_rejected(self):
        target = SKILL / 'not-an-authorized-workspace'
        self.assertFalse(target.exists())
        result = subprocess.run([sys.executable, str(RUNNER), 'prepare', '--workspace', str(target)],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
