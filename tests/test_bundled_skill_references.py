"""Every path a bundled skill tells the user to RUN must actually ship.

The failure is silent and total: a skill says
``python skills/productivity/google-workspace/scripts/setup.py`` while
``seed_skills()`` installs it at ``skills/public/google-workspace/scripts/setup.py``.
Nothing errors at build or install time — the skill dies on its first command, and
only for people who are not the maintainer, whose machine happens to have the file
somewhere else.

Scope, stated honestly because the corpus is mid-migration: 255 bundled skills
carry **285** companion references that do not resolve to a file in this tree.
They are not one problem:

- **156** have their content *inlined* as a ``## Lampiran: `<path>`” appendix. The
  knowledge is present and loadable; only the shell command shown is unrunnable.
- **~92** name a ``references/*.md`` that was deliberately not inlined (the skill's
  own "File pendukung tidak di-inline" note says so). Documentation, not a command.
- **37** name a script. These are the dangerous ones: the skill tells the operator
  to execute a file that does not exist.

So the tests here are a mix of hard invariants for the classes already repaired and
a **ratchet** for the backlog: the total may only ever go down. A single
"everything must resolve" assertion would have to be skipped, and a skipped test
protects nothing.
"""
from __future__ import annotations

import importlib
import os
import py_compile
import re
import subprocess
import sys
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "zeline" / "skills"

COMPANION = re.compile(
    r"(?:zeline/)?skills/(?:public/)?[\w\-.]+/(?:scripts|references|templates|assets)/[\w\-./]+"
    r"|(?<![\w/])(?:scripts|references|templates|assets)/[\w\-./]+\.(?:py|sh|bash|json|md|html|css|js|yaml|yml|txt)"
)
SCRIPT_SUFFIXES = {".py", ".sh", ".bash"}

# Highest number of unresolved companion references allowed. Measured with the
# resolver below, and only ever lowered: raising it means a change made the corpus
# less usable. 265 total, of which 156 are inlined appendices (content present) and
# 47 name a script (25 with content nowhere at all — the dangerous residue).
MAX_UNRESOLVED_REFERENCES = 265
MAX_UNRESOLVED_SCRIPTS = 47

# Prose examples, not runnable paths: a skill about auditing OTHER repositories
# quotes their layouts. Per-reference, not per-skill, so a real break in the same
# file still fails.
DOCUMENTATION_EXAMPLES = {
    ("github-repo-audit/SKILL.md", "skills/research/trade-data-tracker/scripts/add_history.py"),
    ("github-repo-audit/SKILL.md", "zeline/skills/research/trade-data-tracker/scripts/add_history.py"),
    ("github-repo-audit/SKILL.md", "scripts/add_history.py"),
    ("zeline-zenith-z89.md", "skills/z5.md"),
    (
        "jupyter-live-kernel.md",
        "skills/hamelnb/skills/jupyter-live-kernel/scripts/jupyter_live_kernel.py",
    ),
    ("jupyter-live-kernel.md", "skills/jupyter-live-kernel/scripts/jupyter_live_kernel.py"),
}


def _skill_root(md: Path) -> Path:
    """The directory a bare `scripts/...` reference is relative to.

    Inside a folder skill, `references/foo.md` naming `scripts/bar.py` means the
    skill's own `scripts/`, not `references/scripts/`. Resolving against the file's
    own parent reports working references as broken.
    """
    for parent in [md.parent, *md.parent.parents]:
        if parent == SKILLS.parent:
            break
        if (parent / "SKILL.md").is_file():
            return parent
    return SKILLS


def _resolve(reference: str, md: Path) -> Path:
    """Map a reference as written onto a path in this tree."""
    cleaned = reference.strip().strip("`\"'")
    stripped = re.sub(r"^(?:zeline/)?skills/public/", "", cleaned)
    stripped = re.sub(r"^(?:zeline/)?skills/", "", stripped)
    if stripped != cleaned:
        return SKILLS / stripped
    return _skill_root(md) / cleaned


def _unresolved() -> list[tuple[str, str, bool]]:
    """(skill, reference, is_inlined) for every companion path that has no file."""
    out: list[tuple[str, str, bool]] = []
    for md in sorted(SKILLS.rglob("*.md")):
        relative = md.relative_to(SKILLS).as_posix()
        text = md.read_text(encoding="utf-8", errors="ignore")
        for reference in sorted(set(COMPANION.findall(text))):
            cleaned = reference.strip().strip("`\"'")
            if (relative, cleaned) in DOCUMENTATION_EXAMPLES:
                continue
            if _resolve(cleaned, md).exists():
                continue
            out.append((relative, cleaned, f"## Lampiran: `{cleaned}`" in text))
    return out


class FlatToFolderUpgradeTests(unittest.TestCase):
    """The upgrade path for the four skills that became folders.

    `_find_skill()` checks `<name>.md` before `<name>/SKILL.md`, so an install that
    already seeded the flat copy would keep loading it forever and never see the
    companion files. `RETIRED_BUNDLED_SKILL_DIGESTS` must therefore list every
    content digest that shipped flat — while still preserving a copy the user edited.
    """

    CONVERTED = ("excalidraw.md", "github-auth.md", "maps.md", "p5js.md")

    def setUp(self) -> None:
        import tempfile

        self.home = Path(tempfile.mkdtemp(prefix="zl-upgrade-"))
        self._old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = str(self.home)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.skills = importlib.import_module("zeline.skills")
        self.public = self.skills.PUBLIC_SKILLS_DIR
        self.public.mkdir(parents=True, exist_ok=True)
        # The exact bytes a previous version seeded.
        self.flat = {}
        for name in self.CONVERTED:
            self.flat[name] = subprocess.run(
                ["git", "show", f"HEAD:zeline/skills/{name}"],
                capture_output=True,
                check=True,
                cwd=Path(__file__).resolve().parents[1],
            ).stdout

    def tearDown(self) -> None:
        import shutil

        if self._old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def test_an_untouched_flat_copy_is_retired_and_replaced_by_the_folder(self):
        for name, blob in self.flat.items():
            (self.public / name).write_bytes(blob)
        self.skills.seed_skills()
        for name in self.CONVERTED:
            with self.subTest(skill=name):
                self.assertFalse(
                    (self.public / name).exists(),
                    f"{name} survived; the install keeps loading the flat version",
                )
                self.assertTrue((self.public / name.removesuffix(".md") / "SKILL.md").is_file())

    def test_a_customized_flat_copy_is_never_deleted(self):
        """Losing a user's edits to make room for a bundled update is unacceptable."""
        edited = self.public / "maps.md"
        edited.write_bytes(self.flat["maps.md"] + b"\n<!-- my own note -->\n")
        self.skills.seed_skills()
        self.assertTrue(edited.is_file())
        self.assertIn("my own note", edited.read_text(encoding="utf-8"))

    def test_crlf_revisions_are_retired_too(self):
        """A Windows checkout seeds CRLF; that copy is just as stale."""
        for name, blob in self.flat.items():
            (self.public / name).write_bytes(blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.skills.seed_skills()
        for name in self.CONVERTED:
            with self.subTest(skill=name):
                self.assertFalse((self.public / name).exists())


class BundledSkillReferenceTests(unittest.TestCase):
    def test_a_folder_skill_never_points_outside_its_own_companion_dirs(self):
        """The class that was actually broken, and must stay fixed.

        A folder skill owns `scripts/` and `references/`. When its SKILL.md names a
        path under its own directories, that file must exist — this is a command the
        operator will run, not background reading.
        """
        broken: list[str] = []
        for md in sorted(SKILLS.glob("*/SKILL.md")):
            skill = md.parent
            relative = md.relative_to(SKILLS).as_posix()
            text = md.read_text(encoding="utf-8", errors="ignore")
            for reference in sorted(set(COMPANION.findall(text))):
                cleaned = reference.strip().strip("`\"'")
                if (relative, cleaned) in DOCUMENTATION_EXAMPLES:
                    continue
                if Path(cleaned).suffix not in SCRIPT_SUFFIXES:
                    continue
                if not _resolve(cleaned, md).exists():
                    broken.append(f"{relative}: {cleaned}")
        self.assertEqual(broken, [], "folder skill names a script that does not ship")

    def test_no_skill_invents_a_category_directory_in_an_install_path(self):
        """`seed_skills()` copies `zeline/skills/<name>` → `~/.zeline/skills/public/<name>`.

        A path like `skills/productivity/google-workspace/scripts/setup.py` or
        `skills/github/github-auth/scripts/gh-env.sh` resolves on nobody's machine.
        Both really shipped that way and both silently failed for every user.
        """
        invented = re.compile(
            r"skills/(?!public/)[\w\-.]+/[\w\-.]+/(?:scripts|references|templates|assets)/"
        )
        offenders: list[str] = []
        for md in sorted(SKILLS.rglob("*.md")):
            relative = md.relative_to(SKILLS).as_posix()
            exempt = {ref for name, ref in DOCUMENTATION_EXAMPLES if name == relative}
            for number, line in enumerate(md.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if not invented.search(line):
                    continue
                if any(ref in line for ref in exempt):
                    continue
                offenders.append(f"{relative}:{number}: {line.strip()[:90]}")
        self.assertEqual(offenders, [], "use skills/public/<name>/... — the seeded location")

    def test_every_shipped_script_parses(self):
        """A script that does not parse is worse than a missing one: it looks fine."""
        failures: list[str] = []
        for script in sorted(SKILLS.rglob("scripts/*")):
            if not script.is_file():
                continue
            if script.suffix == ".py":
                pyc = script.with_suffix(script.suffix + ".testpyc")
                try:
                    py_compile.compile(str(script), cfile=str(pyc), doraise=True)
                except py_compile.PyCompileError as exc:
                    failures.append(f"{script.relative_to(SKILLS)}: {exc.msg.splitlines()[0][:90]}")
                finally:
                    pyc.unlink(missing_ok=True)
            elif script.suffix in {".sh", ".bash"}:
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, text=True, check=False
                )
                if result.returncode != 0:
                    failures.append(f"{script.relative_to(SKILLS)}: {result.stderr.strip()[:110]}")
        self.assertEqual(failures, [])

    def test_the_unresolved_reference_backlog_only_shrinks(self):
        """A ratchet, because the corpus is mid-migration.

        Skipping the check until the last of 285 references is fixed would protect
        nothing in the meantime. This fails if the count grows, so every new skill
        must ship its companions even while the backlog is worked through.
        """
        unresolved = _unresolved()
        self.assertLessEqual(
            len(unresolved),
            MAX_UNRESOLVED_REFERENCES,
            f"unresolved companion references grew to {len(unresolved)}; "
            f"ship the file or inline it. First few: {[f'{a}: {b}' for a, b, _ in unresolved[:5]]}",
        )
        scripts = [item for item in unresolved if Path(item[1]).suffix in SCRIPT_SUFFIXES]
        self.assertLessEqual(
            len(scripts),
            MAX_UNRESOLVED_SCRIPTS,
            f"skills naming a script that does not ship grew to {len(scripts)}: "
            f"{[f'{a}: {b}' for a, b, _ in scripts[:5]]}",
        )

    def test_the_repaired_skills_resolve_completely(self):
        """The six skills fixed in this pass have zero unresolved references."""
        repaired = {"maps", "excalidraw", "github-auth", "p5js", "google-workspace", "brainstorming"}
        offenders = [
            f"{skill}: {reference}"
            for skill, reference, _inlined in _unresolved()
            if skill.split("/")[0].removesuffix(".md") in repaired
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
