"""Community and contributor documentation is a contract, not decoration.

A public repository is judged on whether someone can figure out how to help. The
files below existed nowhere before 0.2.8, and each assertion here pins a specific
way they silently rot:

* a CONTRIBUTING that documents a command CI does not run,
* an extension guide that advertises a CLI subcommand that does not exist,
* a CHANGELOG whose top entry is not the version being shipped,
* templates GitHub silently ignores because the filename or schema is wrong.

Written for ``unittest``: CI runs ``python -m unittest discover -s tests`` and
pytest is not installed there.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


class CommunityFilePresenceTests(unittest.TestCase):
    """GitHub only surfaces these when they sit at the exact expected path."""

    def test_every_community_health_file_exists(self):
        for relative in (
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "CHANGELOG.md",
            "SECURITY.md",
            "LICENSE",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            "docs/extending.md",
        ):
            with self.subTest(file=relative):
                self.assertTrue((ROOT / relative).is_file(), f"missing {relative}")

    def test_readmes_point_contributors_at_the_process(self):
        """A guide nobody links to is a guide nobody reads."""
        for relative, targets in (
            ("README.md", ("CONTRIBUTING.md", "CHANGELOG.md", "docs/extending.md")),
            ("docs/README.id.md", ("../CONTRIBUTING.md", "../CHANGELOG.md", "extending.md")),
            ("docs/README.zh.md", ("../CONTRIBUTING.md", "../CHANGELOG.md", "extending.md")),
        ):
            text = _read(relative)
            for target in targets:
                with self.subTest(page=relative, target=target):
                    self.assertIn(f"]({target})", text)


class ContributingAccuracyTests(unittest.TestCase):
    """Instructions have to match the repository they describe."""

    def setUp(self) -> None:
        self.text = _read("CONTRIBUTING.md")

    def test_it_documents_the_runner_ci_actually_uses(self):
        """CI runs unittest and has no pytest; telling people otherwise wastes a
        contributor's first pull request.

        The negative half matters more than the positive one: a guide can mention
        unittest in passing and still hand out a ``pytest tests/`` command that
        fails in CI, so no line may present pytest as the way to run the suite.
        """
        workflow = _read(".github", "workflows", "tests.yml")
        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("python -m unittest discover -s tests", self.text)
        self.assertIn("unittest`, not `pytest", self.text)
        offenders = [
            line for line in self.text.splitlines()
            if re.match(r"\s*(python -m )?pytest\b", line)
        ]
        self.assertEqual(offenders, [])

    def test_it_documents_the_same_lint_command_ci_blocks_on(self):
        workflow = _read(".github", "workflows", "tests.yml")
        self.assertIn("ruff check zeline tests", workflow)
        self.assertIn("ruff check zeline tests", self.text)

    def test_every_ci_job_it_promises_is_a_real_job(self):
        """The job table is the part a contributor plans around."""
        workflow = yaml.safe_load(_read(".github", "workflows", "tests.yml"))
        jobs = set(workflow["jobs"])
        for job in ("lint", "test", "windows", "installer-windows", "macos", "package"):
            with self.subTest(job=job):
                self.assertIn(job, jobs)
                self.assertIn(job, self.text)

    def test_every_source_path_in_the_map_exists(self):
        """The 'where things live' table drifts the moment a module moves."""
        missing = [
            path for path in re.findall(r"`(zeline/[\w./]+|docs/[\w./]+|tests/)`", self.text)
            if not (ROOT / path).exists()
        ]
        self.assertEqual(missing, [])

    def test_it_keeps_release_mechanics_out_of_contributor_pull_requests(self):
        """A version bump in a feature PR breaks the ten-file version contract.

        Collapsed whitespace, because prose wraps at 80 columns and a sentence is
        no less true for spanning two lines.
        """
        collapsed = " ".join(self.text.split())
        self.assertIn("Do not include a version bump in a feature pull request", collapsed)

    def test_it_forbids_real_credentials_and_gives_a_safe_fixture_id(self):
        self.assertIn("111222333", self.text)
        self.assertIn("SECURITY.md", self.text)


class ExtendingGuideTests(unittest.TestCase):
    """The extension guide is executable documentation: every command must run."""

    def setUp(self) -> None:
        self.text = _read("docs", "extending.md")

    def test_it_covers_all_four_extension_mechanisms(self):
        for anchor in ("custom-tools", "plugin-hooks", "openapi-tools", "mcp-servers"):
            with self.subTest(anchor=anchor):
                self.assertIn(f"#{anchor}", self.text)

    def test_every_zeline_subcommand_it_shows_is_a_real_subcommand(self):
        """`zeline tools openapi-add` etc. — an invented flag here sends a reader
        straight into an argparse error on their first attempt."""
        documented = {
            tuple(match.split()[1:3])
            for match in re.findall(r"^zeline [a-z-]+ [a-z-]+", self.text, re.MULTILINE)
        }
        self.assertTrue(documented, "no zeline subcommands found in the guide")
        for group, action in sorted(documented):
            with self.subTest(command=f"{group} {action}"):
                completed = subprocess.run(
                    [sys.executable, "-m", "zeline.cli", group, "--help"],
                    cwd=ROOT, capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(action, completed.stdout)

    def test_it_states_the_profile_restriction_that_makes_extensions_safe(self):
        """These run arbitrary local Python; a reader must know `safe` excludes them."""
        self.assertIn("`workspace` and `full` profiles", self.text)
        for module in ("custom_tools", "plugins", "openapi_tools"):
            source = _read("zeline", f"{module}.py")
            with self.subTest(module=module):
                self.assertIn('ALLOWED_PROFILES = frozenset({"workspace", "full"})', source)

    def test_the_documented_tool_prefixes_match_the_code(self):
        self.assertIn('TOOL_PREFIX = "custom_"', _read("zeline", "custom_tools.py"))
        self.assertIn('TOOL_PREFIX = "api_"', _read("zeline", "openapi_tools.py"))
        self.assertIn("custom_<function>", self.text)
        self.assertIn("api_", self.text)

    def test_the_documented_credential_variable_matches_the_code(self):
        source = _read("zeline", "openapi_tools.py")
        self.assertIn('f"ZELINE_OPENAPI_{provider}_{security}"', source)
        self.assertIn("ZELINE_OPENAPI_<FILE>_<SCHEME>", self.text)

    def test_the_documented_hook_names_and_deny_sentinel_match_the_code(self):
        source = _read("zeline", "plugins.py")
        self.assertIn('BEFORE_HOOK = "on_tool_before"', source)
        self.assertIn('AFTER_HOOK = "on_tool_after"', source)
        self.assertIn("def deny(", source)
        for symbol in ("on_tool_before", "on_tool_after", "from zeline.plugins import deny"):
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, self.text)


class ChangelogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read("CHANGELOG.md")

    def test_the_newest_entry_is_the_version_being_shipped(self):
        """Bumping the package without adding its entry publishes a changelog that
        stops one release short."""
        first = re.search(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", self.text, re.MULTILINE)
        self.assertIsNotNone(first)
        pyproject = re.search(
            r'^version = "([^"]+)"', _read("pyproject.toml"), re.MULTILINE
        )
        self.assertIsNotNone(pyproject)
        self.assertEqual(first.group(1), pyproject.group(1))

    def test_versions_are_listed_newest_first(self):
        versions = [
            tuple(int(part) for part in match.split("."))
            for match in re.findall(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", self.text, re.MULTILINE)
        ]
        self.assertEqual(versions, sorted(versions, reverse=True))

    def test_every_reference_link_is_defined(self):
        """`([#210])` renders as literal brackets when the definition is missing."""
        used = set(re.findall(r"\]\((?!http)[^)]*\)|\[([#\d.]+)\](?!\()", self.text))
        used.discard("")
        defined = set(re.findall(r"^\[([#\d.]+)\]: ", self.text, re.MULTILINE))
        self.assertEqual(sorted(used - defined), [])

    def test_pull_request_links_point_at_the_canonical_repo(self):
        for link in re.findall(r"^\[#\d+\]: (\S+)", self.text, re.MULTILINE):
            with self.subTest(link=link):
                self.assertTrue(link.startswith("https://github.com/Mftrferdinand/Zeline/pull/"))


class IssueAndPullRequestTemplateTests(unittest.TestCase):
    """A template with an invalid schema is silently dropped by GitHub."""

    def _template(self, name: str) -> dict:
        return yaml.safe_load(_read(".github", "ISSUE_TEMPLATE", name))

    def test_issue_forms_declare_name_description_and_body(self):
        for name in ("bug_report.yml", "feature_request.yml"):
            data = self._template(name)
            with self.subTest(template=name):
                self.assertIn("name", data)
                self.assertIn("description", data)
                self.assertIsInstance(data["body"], list)
                self.assertTrue(data["body"])
                for field in data["body"]:
                    self.assertIn(field["type"], {
                        "markdown", "input", "textarea", "dropdown", "checkboxes",
                    })
                    if field["type"] != "markdown":
                        self.assertIn("id", field)
                        self.assertIn("label", field["attributes"])

    def test_the_bug_form_asks_for_version_platform_and_reproduction(self):
        labels = " ".join(
            str(field.get("attributes", {}).get("label", ""))
            for field in self._template("bug_report.yml")["body"]
        ).casefold()
        for expected in ("version", "platform", "reproduce"):
            with self.subTest(expected=expected):
                self.assertIn(expected, labels)

    def test_the_bug_form_routes_vulnerabilities_away_from_public_issues(self):
        text = _read(".github", "ISSUE_TEMPLATE", "bug_report.yml")
        self.assertIn("private vulnerability reporting", text)

    def test_blank_issues_are_disabled_and_security_has_a_contact_link(self):
        config = self._template("config.yml")
        self.assertFalse(config["blank_issues_enabled"])
        urls = [entry["url"] for entry in config["contact_links"]]
        self.assertTrue(any("security/advisories" in url for url in urls))
        for url in urls:
            with self.subTest(url=url):
                self.assertIn("Mftrferdinand/Zeline", url)

    def test_the_pull_request_template_demands_verification_not_intent(self):
        text = _read(".github", "PULL_REQUEST_TEMPLATE.md")
        self.assertIn("python -m unittest discover -s tests", text)
        self.assertIn("ruff check zeline tests", text)
        self.assertIn("fails before this change and passes after", text)
        self.assertIn("No version bump", text)


class PyPiAvailabilityClaimTests(unittest.TestCase):
    """Public docs may only advertise an install command that actually works.

    0.2.8 shipped the Trusted Publishing pipeline and the docs announced
    ``uv tool install zeline`` as available in the same breath. The upload then
    failed with ``invalid-publisher`` -- valid OIDC token, no pending publisher
    configured at pypi.org -- so the release notes, three READMEs, the install
    guide, and the changelog all documented a command that fetches nothing.

    ``PYPI_PUBLISHED`` is the single switch. Flip it to ``True`` in the same
    commit that lands the first successful upload, and this class then *requires*
    the docs to document the PyPI route instead of forbidding it. Either way the
    pages cannot disagree with reality, and they cannot disagree with each other.
    """

    PYPI_PUBLISHED = False

    PAGES = (
        "README.md",
        "docs/README.id.md",
        "docs/README.zh.md",
        "docs/installation.md",
        "CHANGELOG.md",
        ".github/RELEASE_NOTES.md",
    )

    # Commands a reader would copy. The pipeline may be *described* in prose --
    # including a disclaimer that names the command it is disclaiming -- so the
    # gate is on the runnable form: a line that STARTS with the command, i.e.
    # something sitting in a fenced block waiting to be pasted into a shell.
    INSTALL_COMMANDS = ("uv tool install zeline", "pip install zeline")

    def _claims(self, relative: str) -> list[str]:
        text = _read(*relative.split("/"))
        runnable = []
        for line in text.splitlines():
            stripped = line.strip()
            for command in self.INSTALL_COMMANDS:
                if stripped.startswith(command) or stripped.startswith(f"$ {command}"):
                    runnable.append(command)
        return runnable

    def test_docs_agree_with_whether_the_package_is_on_pypi(self):
        for relative in self.PAGES:
            claims = self._claims(relative)
            with self.subTest(page=relative):
                if self.PYPI_PUBLISHED:
                    continue
                self.assertEqual(
                    claims, [],
                    f"{relative} hands the reader {claims} but PYPI_PUBLISHED is False",
                )

    def test_at_least_one_page_explains_the_publishing_pipeline(self):
        """Not advertising the command is not the same as hiding the work: the
        OIDC pipeline is a real security property and stays documented."""
        notes = _read(".github", "RELEASE_NOTES.md")
        self.assertIn("Trusted Publishing", notes)
        self.assertIn("API token", notes)

    def test_the_workflow_still_carries_the_publish_job(self):
        """The gate above is about claims in prose, not about removing the job."""
        workflow = yaml.safe_load(_read(".github", "workflows", "release.yml"))
        self.assertIn("publish-pypi", workflow["jobs"])
        job = workflow["jobs"]["publish-pypi"]
        self.assertIn("build-release", job["needs"])
        self.assertEqual(job["permissions"]["id-token"], "write")

    def test_the_upload_is_gated_on_a_probe_instead_of_failing(self):
        """A release must not go red because PyPI has no publisher configured.

        A Trusted Publisher lives in a PyPI account, so the workflow cannot know
        from its own files whether an upload will work. Before this gate,
        ``publish-pypi`` failed with ``invalid-publisher`` on every release and
        painted the `pypi` deployment red on the repository page -- for a release
        whose assets were built, verified, attested, and published. A skipped job
        says "not configured"; a failed job says "broken".
        """
        workflow = yaml.safe_load(_read(".github", "workflows", "release.yml"))
        jobs = workflow["jobs"]
        self.assertIn("check-pypi-publisher", jobs)
        probe = jobs["check-pypi-publisher"]
        publish = jobs["publish-pypi"]

        self.assertIn("check-pypi-publisher", publish["needs"])
        self.assertEqual(
            publish["if"].strip(),
            "needs.check-pypi-publisher.outputs.armed == 'true'",
        )
        self.assertEqual(probe["outputs"]["armed"], "${{ steps.probe.outputs.armed }}")
        self.assertEqual(probe["permissions"]["id-token"], "write")

        # The probe must not declare an environment: `environment:` creates a
        # deployment record, which is the very red badge being removed.
        self.assertNotIn("environment", probe)
        self.assertEqual(publish["environment"]["name"], "pypi")

        script = "\n".join(
            str(step.get("run", "")) for step in probe["steps"]
        )
        # It exchanges a token the same way the publish action does; anything
        # weaker (e.g. GETting the project JSON) tests the wrong thing.
        self.assertIn("/_/oidc/mint-token", script)
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_URL", script)
        # The minted upload token must never be written anywhere.
        self.assertIn("-o /dev/null", script)
        # And the failure path has to tell the operator exactly what to configure.
        for hint in ("pending publisher", "release.yml", "zeline"):
            with self.subTest(hint=hint):
                self.assertIn(hint, script)

    def test_the_registration_instructions_require_a_blank_environment(self):
        """Telling the operator to register ``environment: pypi`` breaks the gate.

        PyPI verifies the ``environment`` claim only when the publisher declares
        one (``_check_environment`` returns True for an empty ground truth), and
        a job emits that claim only by declaring ``environment:`` -- which is the
        deployment record this gate exists to avoid. So the probe, which
        deliberately has no environment, sends no claim: against a publisher
        registered for ``pypi`` its exchange is refused, ``armed`` stays false,
        and the upload is skipped on *every* release even though the publisher is
        otherwise perfect. A blank environment accepts both the probe and the
        environment-scoped publish job.
        """
        workflow = yaml.safe_load(_read(".github", "workflows", "release.yml"))
        jobs = workflow["jobs"]
        for name in ("check-pypi-publisher", "verify-pypi-publisher"):
            script = "\n".join(
                str(step.get("run", "")) for step in jobs[name]["steps"]
            )
            with self.subTest(job=name):
                collapsed = " ".join(script.split())
                # Must not hand out the value that disarms the gate.
                self.assertNotIn("environment : pypi", collapsed)
                self.assertNotIn("Environment name | `pypi`", collapsed)
                # Must say, in the operator's words, to leave it empty.
                self.assertRegex(collapsed, r"(?i)environment[^\n]{0,40}blank")
                # And say why, so nobody "helpfully" fills it in later.
                self.assertIn("deployment record", collapsed)

    def test_a_manual_run_can_verify_the_publisher_without_a_release(self):
        """You must be able to ask "will the next release publish?" for free.

        Only PyPI knows whether a Trusted Publisher matches, and the answer had
        to be discovered by cutting a release -- burning a version number on a
        configuration question. `workflow_dispatch` + verify-pypi-publisher makes
        it a button, and the release path is untouched by it.
        """
        workflow = yaml.safe_load(_read(".github", "workflows", "release.yml"))
        # PyYAML parses a bare `on:` key as the boolean True.
        triggers = workflow[True] if True in workflow else workflow["on"]
        self.assertIn("workflow_dispatch", triggers)
        self.assertIn("push", triggers)

        jobs = workflow["jobs"]
        verify = jobs["verify-pypi-publisher"]
        # Manual-only, so a release never waits on it or reports its result.
        self.assertEqual(verify["if"].strip(), "github.event_name == 'workflow_dispatch'")
        self.assertEqual(jobs["build-release"]["if"].strip(), "github.event_name == 'push'")
        self.assertNotIn("needs", verify)
        # Same reasoning as the release-path probe: `environment:` would create a
        # deployment record, which is the red badge being avoided.
        self.assertNotIn("environment", verify)
        self.assertEqual(verify["permissions"]["id-token"], "write")

        script = "\n".join(str(step.get("run", "")) for step in verify["steps"])
        self.assertIn("/_/oidc/mint-token", script)
        self.assertIn("-o /dev/null", script)
        self.assertIn("GITHUB_STEP_SUMMARY", script)
        # It reports a configuration fact, so it must not go red either.
        self.assertIn("exit 0", script)
        # The failure path has to name every field, including the project name --
        # a mismatch there fails at upload with "Non-user identities cannot
        # create new projects", which reads like a permissions problem and is not.
        for field in ("PyPI Project Name", "Owner", "Repository name",
                      "Workflow name", "Environment name",
                      "pypi.org/manage/account/publishing/"):
            with self.subTest(field=field):
                self.assertIn(field, script)

    def test_when_flipped_the_docs_must_document_the_pypi_route(self):
        """Guards the other direction: once PyPI works, silent docs are the bug."""
        if not self.PYPI_PUBLISHED:
            self.skipTest("PYPI_PUBLISHED is False; the forbidding direction applies")
        for relative in ("README.md", "docs/installation.md"):
            with self.subTest(page=relative):
                self.assertTrue(self._claims(relative), f"{relative} omits the PyPI route")


if __name__ == "__main__":
    unittest.main(verbosity=2)
