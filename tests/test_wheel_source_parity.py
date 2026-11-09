"""A packaged candidate must contain precisely the Python source under review."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/check_wheel_sources.py"


def checker():
    spec = importlib.util.spec_from_file_location("check_wheel_sources", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WheelSourceParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "zeline"
        self.package.mkdir()
        (self.package / "__init__.py").write_bytes(b"version = 1\n")
        (self.package / "tools.py").write_bytes(b"SCHEMA = ('tsx',)\n")
        self.wheel = self.root / "zeline-0.1-py3-none-any.whl"

    def wheel_with(self, files):
        with ZipFile(self.wheel, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)

    def test_exact_candidate_sources_pass(self):
        self.wheel_with({"zeline/__init__.py": b"version = 1\n",
                         "zeline/tools.py": b"SCHEMA = ('tsx',)\n"})
        self.assertEqual(checker().verify_python_sources(self.root, self.wheel), 2)

    def test_stale_tool_schema_fails(self):
        self.wheel_with({"zeline/__init__.py": b"version = 1\n",
                         "zeline/tools.py": b"SCHEMA = ('js',)\n"})
        with self.assertRaisesRegex(ValueError, "changed.*zeline/tools.py"):
            checker().verify_python_sources(self.root, self.wheel)

    def test_missing_or_extra_module_fails(self):
        self.wheel_with({"zeline/__init__.py": b"version = 1\n",
                         "zeline/stray.py": b"UNKNOWN = 1\n"})
        with self.assertRaisesRegex(ValueError, "missing.*zeline/tools.py"):
            checker().verify_python_sources(self.root, self.wheel)
        (self.package / "tools.py").unlink()
        with self.assertRaisesRegex(ValueError, "extra.*zeline/stray.py"):
            checker().verify_python_sources(self.root, self.wheel)

    def test_non_source_wheel_metadata_is_ignored(self):
        self.wheel_with({"zeline/__init__.py": b"version = 1\n",
                         "zeline/tools.py": b"SCHEMA = ('tsx',)\n",
                         "zeline-0.1.dist-info/METADATA": b"Metadata-Version: 2.1\n"})
        self.assertEqual(checker().verify_python_sources(self.root, self.wheel), 2)

    def test_canonical_soul_is_included_and_exact(self):
        (self.package / "SOUL.md").write_bytes("# SOUL.md — Zeline\n".encode())
        python_files = {"zeline/__init__.py": b"version = 1\n",
                        "zeline/tools.py": b"SCHEMA = ('tsx',)\n"}
        self.wheel_with(python_files)
        with self.assertRaisesRegex(ValueError, "missing.*zeline/SOUL.md"):
            checker().verify_python_sources(self.root, self.wheel)
        self.wheel_with({**python_files, "zeline/SOUL.md": b"# old persona\n"})
        with self.assertRaisesRegex(ValueError, "changed.*zeline/SOUL.md"):
            checker().verify_python_sources(self.root, self.wheel)
        self.wheel_with({**python_files, "zeline/SOUL.md": "# SOUL.md — Zeline\n".encode()})
        self.assertEqual(checker().verify_python_sources(self.root, self.wheel), 2)
