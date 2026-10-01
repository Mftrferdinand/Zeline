"""Upgrade repaired nested companions without replacing user modifications."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zeline import skills


class NestedRefreshTests(unittest.TestCase):
    def test_nested_known_copy_updates_and_custom_copy_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public, source = root / 'public', root / 'source'
            name = 'example/templates/helper.py'
            for base, content in ((public, b'old'), (source, b'new')):
                target = base / name
                target.parent.mkdir(parents=True)
                target.write_bytes(content)
            with patch.object(skills, 'PUBLIC_SKILLS_DIR', public), patch.object(
                skills, 'BUNDLED_SKILL_UPDATE_DIGESTS', {name: (hashlib.sha256(b'old').hexdigest(),)}
            ):
                skills._refresh_known_bundled_revisions(source)
                self.assertEqual((public / name).read_bytes(), b'new')
                (public / name).write_bytes(b'custom')
                skills._refresh_known_bundled_revisions(source)
                self.assertEqual((public / name).read_bytes(), b'custom')
