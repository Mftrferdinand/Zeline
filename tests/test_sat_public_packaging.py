"""Public-distribution boundaries for owner-provided solver source."""
import ast
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicSolverPackagingTests(unittest.TestCase):
    def test_manifest_does_not_reintroduce_private_uploads_to_sdist(self):
        from setuptools._distutils.filelist import FileList
        private = ['zeline/skills/zeline-sat-solvers/assets/supplied/fixture/original.py',
                   'zeline/skills/zeline-sat-solvers/assets/adapted/probsab.c']
        public = 'zeline/skills/zeline-sat-solvers/scripts/portfolio.py'
        files = FileList()
        fixtures = private + [public, 'install.sh', 'install.ps1', 'CHANGELOG.md',
            '.github/RELEASE_NOTES.md', 'docs/index.md', 'zeline/SOUL.md',
            'zeline/zenith_tools/example.py', 'zeline/skills/cache.pyc',
            'zeline/skills/__pycache__/cached.pyc']
        # distutils discovers native paths on Windows, then converts template globs.
        files.set_allfiles([str(Path(path)) for path in fixtures])
        for line in (ROOT / 'MANIFEST.in').read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                files.process_template_line(line)
        selected = {Path(path).as_posix() for path in files.files}
        self.assertFalse(set(private) & selected, 'Explicit MANIFEST includes leaked private sources')
        self.assertIn(public, selected, 'Public solver adapters must remain in the sdist')

    def test_private_uploads_are_excluded_from_git_and_package_discovery(self):
        paths = ['zeline/skills/zeline-sat-solvers/assets/supplied/nonce_solver_v3/probsab.c',
                 'zeline/skills/zeline-sat-solvers/assets/adapted/probsab.c']
        # Validate the shipped ignore rules in a disposable repository too: a
        # source-distribution/archive checkout has no .git directory of its own.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory)
            (probe / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
            subprocess.run(['git', 'init', '--quiet', str(probe)],
                           capture_output=True, check=True, timeout=5)
            for path in paths:
                check = subprocess.run(['git', 'check-ignore', '--no-index', path],
                                       cwd=probe, capture_output=True, text=True, timeout=5)
                self.assertEqual(check.returncode, 0, path)
        # These literal lists use Python-compatible TOML array syntax; avoid a 3.11-only dependency.
        # Fresh wheel/sdist builds separately validate actual setuptools configuration semantics.
        text = (ROOT / 'pyproject.toml').read_text()
        match = re.search(r'(?m)^exclude\s*=\s*(\[.*\])\s*$', text)
        assert match is not None, 'missing package discovery exclusions'
        excludes = ast.literal_eval(match.group(1))
        self.assertIn('zeline.skills*', excludes)
        self.assertIn('zeline.skills.zeline-sat-solvers.assets.supplied*', excludes)
        self.assertIn('zeline.skills.zeline-sat-solvers.assets.adapted*', excludes)
        match = re.search(r'(?m)^"\*"\s*=\s*(\[.*\])\s*$', text)
        assert match is not None, 'missing package data exclusions'
        data = ast.literal_eval(match.group(1))
        self.assertIn('skills/zeline-sat-solvers/assets/supplied/*', data)
        self.assertIn('skills/zeline-sat-solvers/assets/adapted/*', data)


if __name__ == '__main__':
    unittest.main()
