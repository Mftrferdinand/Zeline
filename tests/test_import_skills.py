"""Kontrak scrub branding importer skill.

Importer menulis ke korpus skill publik. Kalau scrub-nya bocor, branding
upstream masuk kembali lewat pintu belakang setiap kali skill baru di-import,
jadi guard-nya diuji di sini — bukan cuma hasil akhir korpus.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_importer():
    """Muat scripts/import_skills.py: bukan package, jadi lewat spec loader."""
    path = ROOT / "scripts" / "import_skills.py"
    spec = importlib.util.spec_from_file_location("_zeline_import_skills", path)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest(f"tidak bisa memuat {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load_importer()


class BrandScrubTests(unittest.TestCase):
    def test_scrub_covers_every_forbidden_term(self):
        """Tiap term terlarang harus benar-benar tersapu, bukan cuma terdaftar."""
        samples = {
            "hermes": "Jalankan lewat Hermes Agent di ~/.hermes/skills.",
            "openclaw": "OpenClaw memakai OPENCLAW_HOME dan ClawHub.",
            "superagent": "SUPERAGENT V7 IRONCLAW pakai ~/.superagent/ledger.db.",
            "ironclaw": "Codename Ironclaw, lihat superagent-v7 pack.",
        }
        for term, sample in samples.items():
            with self.subTest(term=term):
                scrubbed = importer._scrub_brand(sample)
                self.assertNotIn(term, scrubbed.casefold())

    def test_forbidden_terms_include_renamed_corpus_branding(self):
        for term in ("hermes", "openclaw", "clawhub", "aesora", "superagent", "ironclaw"):
            with self.subTest(term=term):
                self.assertIn(term, importer.FORBIDDEN_TERMS)

    def test_scrub_maps_upstream_env_prefix_to_zeline(self):
        scrubbed = importer._scrub_brand("Set HERMES_HOME dan SUPERAGENT_LEDGER_DB.")
        self.assertIn("ZELINE_HOME", scrubbed)
        self.assertIn("ZELINE_ZENITH_LEDGER_DB", scrubbed)


class ConvertGuardTests(unittest.TestCase):
    def _write_skill(self, root: Path, body: str) -> Path:
        folder = root / "demo-skill"
        folder.mkdir(parents=True)
        skill = folder / "SKILL.md"
        skill.write_text(
            f"---\nname: demo-skill\ndescription: Contoh skill.\n---\n\n# Demo\n\n{body}\n",
            encoding="utf-8",
        )
        return skill

    def test_convert_rejects_output_that_still_leaks_branding(self):
        """Term yang tak tercakup BRAND_SCRUBS harus menggagalkan konversi."""
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._write_skill(Path(tmp), "Dibangun di atas Aesora runtime.")
            name, status = importer.convert_one(str(skill), force=True, dry=True)
        self.assertEqual(name, "demo-skill")
        self.assertTrue(status.startswith("FAIL"), status)
        self.assertIn("aesora", status)

    def test_convert_passes_after_scrub_rewrites_known_branding(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = self._write_skill(
                Path(tmp), "Jalankan SUPERAGENT V7 IRONCLAW lewat Hermes Agent."
            )
            name, status = importer.convert_one(str(skill), force=True, dry=True)
        self.assertEqual(name, "demo-skill")
        self.assertTrue(status.startswith("DRY"), status)

    def test_guard_runs_before_any_file_is_written(self):
        """FAIL tidak boleh menyisakan file setengah jadi di direktori tujuan."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = self._write_skill(root, "Pakai Aesora sebagai host.")
            destination = root / "public"
            with mock.patch.object(importer, "ZELINE_PUBLIC", str(destination)):
                _name, status = importer.convert_one(str(skill), force=True, dry=False)
            self.assertTrue(status.startswith("FAIL"), status)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
