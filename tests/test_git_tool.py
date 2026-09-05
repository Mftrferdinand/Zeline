"""Contract tests for the structured git tool.

Before this, every git operation went through ``run_shell``, which is owner-only —
so an agent that could read and write files in a repository could not run ``git
status``. It had to guess whether its own edits were staged and could not show the
operator a diff of what it had just changed.

The tests below pin three things: that a ``workspace`` agent can now inspect and
record work, that the operations which could destroy an operator's uncommitted work
are refused *by name*, and that nothing here goes through a shell — a path
containing ``;`` or a backtick is a path.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _git_available() -> bool:
    return shutil.which("git") is not None


@unittest.skipUnless(_git_available(), "git is not installed on this runner")
class GitToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="zl-git-home-"))
        self._old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = str(self.home)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.vcs = importlib.import_module("zeline.vcs")
        self.tools = importlib.import_module("zeline.tools")
        self.repo = Path(tempfile.mkdtemp(prefix="zl-git-repo-"))
        self._sh("init", "-q")
        self._sh("config", "user.email", "test@example.com")
        self._sh("config", "user.name", "Test")
        # `workspace`, deliberately: this is the profile that had no git at all.
        self.executor = self.tools.ToolExecutor(
            "telegram:4242", profile="workspace", workspace=str(self.repo)
        )

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.repo, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def _sh(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=False)

    def _write(self, name: str, text: str = "content\n") -> Path:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def git(self, **kwargs) -> str:
        return self.executor.run("git", kwargs)

    # -- availability, the reason this exists
    def test_a_workspace_agent_can_use_git_without_a_shell(self):
        definition = next(d for d in self.tools.TOOL_DEFS if d.name == "git")
        self.assertEqual(sorted(definition.profiles), ["full", "workspace"])
        run_shell = next(d for d in self.tools.TOOL_DEFS if d.name == "run_shell")
        # The whole point: run_shell is owner-only, so git had to be reachable
        # some other way for a repo-capable agent to be useful.
        self.assertNotIn("workspace", run_shell.profiles)
        self.assertIn("On branch", self.git(action="status"))

    def test_a_public_safe_gateway_still_has_no_git(self):
        denied = self.tools.ToolExecutor(
            "telegram:public", profile="safe", workspace=str(self.repo)
        ).run("git", {"action": "status"})
        self.assertIn("not allowed for profile", denied.lower())

    # -- reads
    def test_status_separates_staged_unstaged_and_untracked(self):
        self._write("tracked.py")
        self._sh("add", "tracked.py")
        self._sh("commit", "-qm", "first")
        self._write("tracked.py", "changed\n")
        self._write("fresh.md")
        self._sh("add", "fresh.md")
        result = self.git(action="status")
        self.assertIn("Staged (1)", result)
        self.assertIn("fresh.md", result)
        self.assertIn("Not staged (1)", result)
        self.assertIn("tracked.py", result)

    def test_status_on_a_repository_with_no_commits_does_not_leak_a_git_error(self):
        """`rev-parse --abbrev-ref HEAD` fails before the first commit.

        Its fatal message was landing in the operator's status output, which reads
        as a broken tool rather than an empty repository.
        """
        result = self.git(action="status")
        self.assertNotIn("fatal", result)
        self.assertNotIn("ambiguous argument", result)
        self.assertIn("Working tree clean", result)

    def test_an_unstaged_change_is_not_reported_as_staged(self):
        """`--porcelain` codes are POSITIONAL: an unstaged edit starts with a space.

        Stripping the output shifted every code left by one, so ` M` read as `M `
        (staged) and the path lost its first character. `commit` then believed there
        was something staged and handed git a commit it had to reject.
        """
        self._write("app.py")
        self._sh("add", "app.py")
        self._sh("commit", "-qm", "first")
        self._write("app.py", "changed\n")
        entries = self.vcs._status_lines(self.repo)
        self.assertEqual(entries, [(" M", "app.py")])
        self.assertIn("Not staged", self.git(action="status"))
        self.assertNotIn("Staged (", self.git(action="status"))

    def test_diff_distinguishes_staged_from_unstaged(self):
        self._write("app.py")
        self._sh("add", "app.py")
        self._sh("commit", "-qm", "first")
        self._write("app.py", "unstaged change\n")
        self.assertIn("unstaged change", self.git(action="diff"))
        self.assertIn("No staged changes", self.git(action="diff", staged=True))
        self._sh("add", "app.py")
        self.assertIn("unstaged change", self.git(action="diff", staged=True))

    def test_log_and_show_and_branch_work(self):
        self._write("app.py")
        self._sh("add", "app.py")
        self._sh("commit", "-qm", "feat: the first thing")
        self.assertIn("feat: the first thing", self.git(action="log"))
        self.assertIn("feat: the first thing", self.git(action="show"))
        self.assertIn("Current branch:", self.git(action="branch"))

    def test_a_nonsense_ref_is_refused_before_reaching_git(self):
        for ref in ("--upload-pack=touch /tmp/x", "-x", "a b; rm -rf /"):
            with self.subTest(ref=ref):
                result = self.git(action="show", ref=ref)
                self.assertIn("ERROR git", result)

    # -- writes
    def test_add_then_commit_records_the_work(self):
        self._write("app.py")
        self.assertIn("Staged 1 file(s)", self.git(action="add", path="app.py"))
        result = self.git(action="commit", message="feat: add app")
        self.assertIn("Committed", result)
        _, log = self.vcs._run(["log", "--oneline"], self.repo)
        self.assertIn("feat: add app", log)

    def test_committing_with_nothing_staged_says_what_to_do(self):
        """git's own message here is a screenful of hints for a human at a terminal."""
        self._write("app.py")
        self._sh("add", "app.py")
        self._sh("commit", "-qm", "first")
        self._write("app.py", "changed but not staged\n")
        result = self.git(action="commit", message="x")
        self.assertIn("nothing is staged", result)
        self.assertIn("action='add'", result)
        self.assertNotIn("use \"git add", result)

    def test_an_empty_commit_message_is_refused(self):
        self._write("app.py")
        self.git(action="add", path="app.py")
        self.assertIn("needs a message", self.git(action="commit", message="   "))

    # -- the refusals are the feature
    def test_history_rewriting_actions_are_refused_by_name_with_a_reason(self):
        for verb in ("push", "pull", "reset", "checkout", "clean", "stash", "rebase", "tag"):
            with self.subTest(verb=verb):
                result = self.git(action=verb)
                self.assertIn("ERROR git", result)
                self.assertIn(verb, result)
                # A reason, and the escape hatch: a generic "unknown action" would
                # have the model trying variations of the same verb.
                self.assertIn("because it", result)
                self.assertIn("run_shell", result)

    def test_an_unknown_action_lists_the_real_ones(self):
        result = self.git(action="teleport")
        self.assertIn("unknown action", result)
        for verb in self.vcs.ACTIONS:
            self.assertIn(verb, result)

    def test_the_refused_set_never_overlaps_the_allowed_set(self):
        self.assertEqual(set(self.vcs.REFUSED) & set(self.vcs.ACTIONS), set())

    # -- no shell, ever
    def test_a_path_with_shell_metacharacters_is_just_a_path(self):
        """Through run_shell this would execute; here it must stage a file."""
        name = "we;ird`file$(touch pwned).txt"
        self._write(name)
        result = self.git(action="add", path=name)
        self.assertIn("Staged", result)
        self.assertFalse((self.repo / "pwned").exists())

    def test_a_commit_message_cannot_inject_a_command(self):
        self._write("app.py")
        self.git(action="add", path="app.py")
        self.git(action="commit", message='x"; touch pwned; echo "')
        self.assertFalse((self.repo / "pwned").exists())
        _, log = self.vcs._run(["log", "-1", "--pretty=%s"], self.repo)
        self.assertIn("touch pwned", log)  # stored as TEXT, not executed

    def test_escaping_the_workspace_is_refused(self):
        result = self.git(action="add", path="../../etc/passwd")
        self.assertIn("inside the workspace", result)

    def test_a_directory_that_is_not_a_repository_says_so(self):
        plain = Path(tempfile.mkdtemp(prefix="zl-git-plain-"))
        try:
            result = self.tools.ToolExecutor(
                "telegram:4242", profile="workspace", workspace=str(plain)
            ).run("git", {"action": "status"})
            self.assertIn("not inside a git repository", result)
        finally:
            shutil.rmtree(plain, ignore_errors=True)

    # -- credentials
    def test_staging_a_credential_file_warns_and_committing_it_is_refused(self):
        """Warning alone is not enough: undoing a committed secret needs history rewriting.

        Which is exactly what this module will not do, so the refusal has to happen
        before the commit exists.
        """
        self._write(".env", "SECRET=abc\n")
        staged = self.git(action="add", path=".env")
        self.assertIn("WARNING", staged)
        self.assertIn(".env", staged)
        blocked = self.git(action="commit", message="add config")
        self.assertIn("refusing to commit", blocked)
        _, log = self.vcs._run(["log", "--oneline"], self.repo)
        self.assertNotIn("add config", log)

    def test_the_secret_patterns_cover_the_usual_files(self):
        for name in (
            ".env", ".env.local", "config/.env",
            "id_rsa", "deploy/id_ed25519",
            "server.pem", "cert.key", "store.jks",
            "credentials.json", "secrets.yml", "tokens.txt",
            ".npmrc", ".netrc", "gcp-service-account.json",
        ):
            with self.subTest(name=name):
                self.assertTrue(self.vcs.looks_secret(name))
        for name in ("app.py", "README.md", "environment.md", "keyboard.ts", "monkey.json"):
            with self.subTest(name=name):
                self.assertFalse(self.vcs.looks_secret(name))

    # -- robustness
    def test_a_missing_git_binary_is_reported_plainly(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            result = self.git(action="status")
        self.assertIn("git is not installed", result)

    def test_a_hanging_git_cannot_hang_the_turn(self):
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=self.vcs.TIMEOUT_SECONDS),
        ):
            result = self.git(action="status")
        self.assertIn("timed out", result)

    def test_git_is_never_allowed_to_prompt(self):
        """A credential or editor prompt would wedge the turn with nobody to answer."""
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args, 0, "true", "")

        with mock.patch("subprocess.run", side_effect=fake_run):
            self.git(action="status")
        self.assertEqual(captured["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertFalse(captured.get("shell", False))


class GitProgressFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_each_action_has_its_own_feed_line(self):
        cases = {
            ("status", "", ""): "🌿 Checking git status…",
            ("diff", "", ""): "🔍 Reading the diff…",
            ("log", "", ""): "📜 Reading commit history…",
            ("show", "", ""): "🔎 Reading a commit…",
            ("branch", "", ""): "🌿 Listing branches…",
            ("add", "zeline/tools.py", ""): "➕ Staging <code>tools.py</code>",
            ("commit", "", "feat: a thing\n\nbody"): "💾 Committing: feat: a thing",
        }
        for (action, path, message), expected in cases.items():
            with self.subTest(action=action):
                line = self.telegram._tool_progress_text(
                    "git", {"action": action, "path": path, "message": message}
                )
                self.assertEqual(line, expected)
                self.assertFalse(line.startswith("🔧"))


if __name__ == "__main__":
    unittest.main()
