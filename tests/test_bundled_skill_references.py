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


def _usable_bash() -> str | None:
    """Path to a bash that can actually syntax-check a file, or None.

    Not merely `which("bash")`. On Windows runners the bash on PATH may be a WSL
    stub or a Git-for-Windows build that cannot see a `D:\\a\\...` path the way
    Python spells it — the observed failure was `bash -n` exiting non-zero with
    *empty* stderr for seven scripts that are valid on Linux and macOS. A green
    check that silently proves nothing is worse than an honest skip, so this probes
    a known-good file and only returns bash if the probe passes.

    Skipping the shell half there is correct: .sh syntax does not vary by host OS,
    and the Linux/macOS jobs check it. The Python half still runs everywhere, which
    the test asserts separately.
    """
    import shutil

    found = shutil.which("bash")
    if not found:
        return None
    probe = SKILLS / "p5js" / "scripts" / "serve.sh"
    if not probe.is_file():
        return found
    result = subprocess.run(
        [found, "-n", probe.as_posix()], capture_output=True, text=True, check=False
    )
    return found if result.returncode == 0 else None


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

    The digests are hardcoded rather than read back out of git. Reading them from
    `HEAD` passes locally before the commit and then fails in CI, because after the
    commit those paths no longer exist at `HEAD` — the test would only ever work in
    the working tree that created it. Hardcoding matches how
    `test_zenith_cross_reference_update_map_covers_pre_fix_revisions` pins its own
    revisions, and the mechanism itself is exercised separately below with content
    this test owns.
    """

    # sha256(filename) → sha256 of the flat content that shipped (LF, then CRLF).
    RETIRED = {
        "excalidraw.md": (
            "b08e844e4e9152e2b91023d056e103a5d90dcc8c6170a586dd8fd71bdc4298b0",
            (
                "7c7f3e08c6e399ff1b3fc2ce06f98e8c0a609e47696460cc58dca0ef672cee11",
                "6f3be51527ca53b44d29d77c5790cf57925283598906eb9722674d63fda6b692",
            ),
        ),
        "github-auth.md": (
            "c5b8b21d138f859db534e8f6963749d1d0fdb3bb46f01a2df7e60225ed69675b",
            (
                "a99a9fb20d9669542aa8410b48993023ca5a40bbfdf9100e3ce92ec347f20d89",
                "c279f2ebf85b8e5fd5db4f8abd96f651d8b44d2dfdac7406dcdb3c7bdf836546",
            ),
        ),
        "maps.md": (
            "46dbb8fc8f2f62f02d43f7be721f173eb9ee2308dd08b8c9e3162109f06062e3",
            (
                "cc1eb650625824695ddf7b058d9b0fc78d3c16f782277e2fabf3a46484048e34",
                "c0e39bbe525ccbc25ba13e6e020b105432e7582dfcb57147c6cbcabb209c428f",
            ),
        ),
        "p5js.md": (
            "4e44193e36cedab0ffe2509872260c4b961eabac911b88b50900b5725d82cc34",
            (
                "fe4ac4983bc33a811b0ad3c221513ccd5dcd797ee0a6803c7e5734239fd6fec8",
                "4281cb904dc78fe1a3f5769350a4921aee34bb72f24250c9cac2f9a82c724ac6",
            ),
        ),
    }

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

    def tearDown(self) -> None:
        import shutil

        if self._old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def test_each_converted_skill_is_registered_for_retirement(self):
        """Missing an entry means the stale flat copy wins forever, silently."""
        import hashlib

        table = self.skills.RETIRED_BUNDLED_SKILL_DIGESTS
        for name, (filename_digest, content_digests) in self.RETIRED.items():
            with self.subTest(skill=name):
                self.assertEqual(hashlib.sha256(name.encode()).hexdigest(), filename_digest)
                self.assertIn(filename_digest, table)
                # Both line-ending revisions: the package ships LF, a Windows
                # checkout of the repo can seed CRLF, and only listed digests are
                # removed.
                for digest in content_digests:
                    self.assertIn(digest, table[filename_digest])

    def test_each_converted_skill_now_seeds_as_a_folder(self):
        self.skills.seed_skills()
        for name in self.RETIRED:
            stem = name.removesuffix(".md")
            with self.subTest(skill=stem):
                self.assertTrue((self.public / stem / "SKILL.md").is_file())
                self.assertFalse((self.public / name).exists())

    def test_an_untouched_seeded_copy_is_retired_on_upgrade(self):
        """The mechanism, exercised with content this test owns end to end."""
        import hashlib
        from unittest import mock

        stale = self.public / "obsolete-skill.md"
        body = b"# Obsolete\n\nSuperseded by a folder skill.\n"
        stale.write_bytes(body)
        filename_digest = hashlib.sha256(stale.name.encode()).hexdigest()
        with mock.patch.dict(
            self.skills.RETIRED_BUNDLED_SKILL_DIGESTS,
            {filename_digest: (hashlib.sha256(body).hexdigest(),)},
            clear=True,
        ):
            self.skills.seed_skills()
        self.assertFalse(stale.exists(), "an untouched retired copy must be removed")

    def test_a_customized_copy_is_never_deleted(self):
        """Losing a user's edits to make room for a bundled update is unacceptable."""
        import hashlib
        from unittest import mock

        edited = self.public / "obsolete-skill.md"
        shipped = b"# Obsolete\n\nSuperseded by a folder skill.\n"
        edited.write_bytes(shipped + b"\n<!-- my own note -->\n")
        filename_digest = hashlib.sha256(edited.name.encode()).hexdigest()
        with mock.patch.dict(
            self.skills.RETIRED_BUNDLED_SKILL_DIGESTS,
            {filename_digest: (hashlib.sha256(shipped).hexdigest(),)},
            clear=True,
        ):
            self.skills.seed_skills()
        self.assertTrue(edited.is_file())
        self.assertIn("my own note", edited.read_text(encoding="utf-8"))


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
        bash = _usable_bash()
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
            elif script.suffix in {".sh", ".bash"} and bash:
                result = subprocess.run(
                    [bash, "-n", script.as_posix()], capture_output=True, text=True, check=False
                )
                if result.returncode != 0:
                    failures.append(f"{script.relative_to(SKILLS)}: {result.stderr.strip()[:110]}")
        self.assertEqual(failures, [])
        # The Python half must have run regardless, or a green result here would be
        # meaningless on a runner without a shell.
        self.assertTrue(any(p.suffix == ".py" for p in SKILLS.rglob("scripts/*")))

    def test_a_blind_skill_rename_never_corrupts_code_examples(self):
        """PR #141 renamed x1-x7 → z29-z35 with a global textual replace.

        It could not tell a skill reference from an ordinary identifier, so every
        `x1`, `x2`, ... appearing as a VARIABLE in a code example was rewritten too:
        `line(z29, y1, z30, y2)`, `box = np.array([z29, y1, z30, y2])`,
        `<line z29="4" .../>`. None of that is valid p5.js, numpy, or SVG — the
        skills were teaching broken code, and it survived because nothing checks
        example bodies.

        `zN` is a legitimate zenith skill ID, so the rule cannot be "no zN". The
        signal is a zN token sharing a line with a coordinate sibling (`x0`, `y1`,
        ...): skill references never appear next to those. Validated by
        re-corrupting all six affected files exactly as #141 did — the rule flagged
        29 lines across 6/6 files with zero false positives on the clean tree.
        """
        coordinate_token = re.compile(r"\bz(?:[12]?[0-9]|3[0-5])\b")
        sibling = re.compile(r"\b[xy][0-9]\b")
        offenders: list[str] = []
        for md in sorted(SKILLS.rglob("*.md")):
            relative = md.relative_to(SKILLS).as_posix()
            # The zenith corpus is *named* zN; its own cross-references are fine.
            if relative.startswith("zeline-zenith-") or relative == "ZENITH_INDEX.md":
                continue
            for number, line in enumerate(
                md.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if coordinate_token.search(line) and sibling.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()[:80]}")
        self.assertEqual(
            offenders,
            [],
            "a skill rename overwrote identifiers inside code examples; rename skill "
            "references by exact filename, never by bare token",
        )

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
