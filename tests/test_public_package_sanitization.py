import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "zeline" / "skills"


class PublicPackageSanitizationTests(unittest.TestCase):
    def test_bundled_skills_exclude_private_identifiers_and_state(self):
        forbidden = {
            "payment phone": "0831" + "87925822",
            "private VPS": "103.125" + ".216.215",
            "legacy shop domain": "kedaicode" + ".shop",
            "legacy support bot": "carecode" + "bot",
            "personal password file": ".tg" + "_pw",
            "personal project": "kedaicode" + "-miniapp",
            "local router endpoint": "localhost:" + "20128",
            "duplicate Zenith prefix": "zeline-zenith-" * 2,
        }
        offenders = []
        for path in sorted(SKILLS.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").casefold()
            except UnicodeDecodeError:
                continue
            for label, value in forbidden.items():
                if value.casefold() in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {label}")
        self.assertEqual(offenders, [])

    def test_auto_register_documentation_uses_obvious_placeholders(self):
        text = (SKILLS / "auto-register" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("user@example.com", text)
        self.assertIn("CHANGE_ME", text)
        emails = re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text, re.I)
        self.assertEqual(set(emails), {"user@example.com"})
        self.assertNotRegex(text, r"\+1\s*\d{10}\b")

    def test_public_readmes_do_not_link_to_unresolved_domains(self):
        dead_domains = ("zero" + "linear.com", "zeline." + "zerolinear.com")
        for path in (ROOT / "README.md", ROOT / "docs/README.id.md", ROOT / "docs/README.zh.md"):
            text = path.read_text(encoding="utf-8")
            for domain in dead_domains:
                self.assertNotIn(f"https://{domain}", text, path)


if __name__ == "__main__":
    unittest.main()
