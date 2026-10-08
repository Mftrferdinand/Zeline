"""Tests untuk skill deep-research (zeline/skills/deep-research/SKILL.md).

- Discoverability memakai mekanisme ASLI: seed_skills() -> load_skill("deep-research")
  lewat _find_skill, dengan SKILLS dir dialihkan ke tempdir (tidak menyentuh ~/.zeline).
- Kelengkapan struktur: assert substring/heading pada isi file, bukan sekadar file exists.
- Nama tool yang disebut di Quick Reference di-cross-check ke definisi ToolDef asli.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import zeline.skills  # noqa: E402

SKILL_DIR = REPO / "zeline" / "skills" / "deep-research"
SKILL_MD = SKILL_DIR / "SKILL.md"


def _seed_into_temp(testcase) -> None:
    """Seed hanya skill deep-research ke tempdir lewat jalur seed_skills asli."""
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    tmp_path = Path(tmp.name)
    public = tmp_path / "skills" / "public"
    private = tmp_path / "skills" / "private"

    # Minimal source tree berisi SATU skill folder (jalur folder SKILL.md asli).
    source = tmp_path / "srcskills"
    shutil.copytree(SKILL_DIR, source / "deep-research")

    testcase.patch_public = mock.patch.object(zeline.skills, "PUBLIC_SKILLS_DIR", public)
    testcase.patch_private = mock.patch.object(zeline.skills, "PRIVATE_SKILLS_DIR", private)
    testcase.patch_public.start()
    testcase.patch_private.start()
    testcase.addCleanup(testcase.patch_public.stop)
    testcase.addCleanup(testcase.patch_private.stop)

    copied = zeline.skills.seed_skills(source=source)
    assert copied == 1, f"seed_skills should copy exactly 1 skill, got {copied}"
    return None


class TestDeepResearchDiscoverable(unittest.TestCase):
    def setUp(self):
        _seed_into_temp(self)

    def test_load_skill_returns_content_not_error(self):
        content = zeline.skills.load_skill("deep-research")
        self.assertFalse(
            content.startswith("ERROR"),
            f"load_skill('deep-research') failed: {content[:120]}",
        )

    def test_load_skill_returns_full_playbook(self):
        content = zeline.skills.load_skill("deep-research")
        self.assertIn("# Deep Research", content)
        # Isi yang di-seed identik dengan file di repo.
        self.assertEqual(content, SKILL_MD.read_text(encoding="utf-8"))

    def test_exact_name_match_no_fuzzy_ambiguity(self):
        # Nama persis "deep-research" harus resolve via exact match folder/SKILL.md.
        content = zeline.skills.load_skill("Deep Research")
        self.assertFalse(content.startswith("ERROR"))


class TestDeepResearchStructure(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        self.lower = self.text.lower()

    def test_file_exists_and_nontrivial(self):
        self.assertTrue(SKILL_MD.is_file())
        self.assertGreater(len(self.text), 2000, "SKILL.md terlalu pendek untuk playbook")

    def test_house_format(self):
        lines = self.text.splitlines()
        self.assertTrue(lines[0].startswith("# Deep Research"))
        # Ringkasan satu-baris berbentuk blockquote harus ada SEBELUM section
        # pertama — tanpa mengasumsikan nomor barisnya (baris bisa bergeser
        # saat playbook diedit).
        first_section = next(
            i for i, line in enumerate(lines) if line.startswith("## ")
        )
        quote_lines = [
            line for line in lines[:first_section] if line.startswith("> ")
        ]
        self.assertTrue(
            quote_lines,
            "tidak ada baris ringkasan '> ...' sebelum section pertama",
        )
        self.assertIn("## Quick Reference", self.text)

    def test_when_to_use_section(self):
        self.assertIn("web_search", self.lower)
        self.assertIn("kapan pakai", self.lower)

    def test_query_splitting_section(self):
        self.assertIn("sub-query", self.lower)
        # Contoh konkret cara memecah topik harus ada.
        self.assertIn("contoh", self.lower)

    def test_memory_first_section(self):
        self.assertIn("memory", self.lower)
        self.assertIn("list_memory", self.text)
        # Alasan kenapa wajib (mencegah jawaban bertentangan dengan pengetahuan user).
        self.assertIn("bertentangan", self.lower)

    def test_cross_check_section(self):
        self.assertIn("cross-check", self.lower)
        self.assertIn("2 sumber", self.lower)
        self.assertIn("independen", self.lower)

    def test_citation_and_certainty_section(self):
        self.assertIn("sitasi", self.lower)
        self.assertIn("belum pasti", self.lower)
        self.assertIn("pasti", self.lower)

    def test_anti_pattern_section(self):
        self.assertIn("anti-pattern", self.lower)
        self.assertIn("tidak tahu", self.lower)

    def test_no_forbidden_branding(self):
        # Kata dilarang dibangun via konkatenasi (pola yang sama dipakai
        # test_security_hygiene) agar literalnya tidak memicu hygiene scan.
        self.assertNotIn("her" + "mes", self.lower, "nama netral: dilarang menyebut nama produk lain")


class TestDeepResearchToolNames(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        tools_py = (REPO / "zeline" / "tools.py").read_text(encoding="utf-8")
        self.defined_tools = set(re.findall(r'ToolDef\(\s*"([^"]+)"', tools_py))
        self.assertGreater(len(self.defined_tools), 10)

    def test_referenced_tools_exist(self):
        # Setiap pemanggilan tool gaya `nama_tool(` di SKILL.md harus terdefinisi.
        candidates = set(re.findall(r"(?m)^(?:`| {0,3})(\w+)\(", self.text))
        candidates |= set(re.findall(r"`(\w+)\([^`]*\)`", self.text))
        for name in sorted(candidates):
            with self.subTest(tool=name):
                self.assertIn(
                    name,
                    self.defined_tools,
                    f"tool '{name}' disebut di SKILL.md tapi tidak ada di zeline/tools.py",
                )

    def test_key_tools_present(self):
        for tool in ("web_search", "web_fetch", "browser", "list_memory", "load_skill"):
            with self.subTest(tool=tool):
                self.assertIn(tool, self.text)
                self.assertIn(tool, self.defined_tools)

    def test_add_memory_arg_name(self):
        # ToolDef add_memory memakai arg "fact", bukan "text".
        self.assertIn('add_memory(fact="', self.text)
        self.assertNotIn("add_memory(text=", self.text)


class TestDeepResearchNoMissingCompanions(unittest.TestCase):
    def test_no_unshipped_script_references(self):
        # Skill ini tidak boleh merujuk scripts/references yang tidak ikut di-ship
        # (melindungi ratchet test_bundled_skill_references).
        text = SKILL_MD.read_text(encoding="utf-8")
        refs = re.findall(
            r"(?<![\w/])(?:scripts|references|templates|assets)/[\w\-./]+\."
            r"(?:py|sh|bash|json|md|html|css|jsx?|tsx?|yaml|yml|txt)(?![\w.])",
            text,
        )
        self.assertEqual(refs, [], f"referensi companion tak terkirim: {refs}")


if __name__ == "__main__":
    unittest.main()
