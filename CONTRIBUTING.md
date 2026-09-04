# Contributing to Zeline

Thanks for wanting to improve Zeline. This page is the actual process, not a
formality: `main` is protected, CI is the gate, and a pull request that skips the
steps below will simply fail.

## Ground rules

- **`main` is protected.** Work on a branch and open a pull request. Direct
  pushes are rejected.
- **Tests are not optional.** A behaviour change without a test that fails
  before the fix and passes after it is not finished.
- **`unittest`, not `pytest`.** CI runs `python -m unittest discover -s tests`.
  `pytest` is not installed there, so a test that only works under `pytest`
  passes locally and fails in CI.
- **No secrets, no personal data.** Provider keys, bot tokens, real chat IDs,
  personal emails, and private hostnames do not belong in code, tests, docs, or
  commit messages. Use an obviously fake chat id such as `111222333` in gateway
  fixtures.

## Set up

```bash
gh repo fork Mftrferdinand/Zeline --clone --remote   # or clone your own fork
cd Zeline
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

`-e` matters: the CLI you run is then the code you are editing, so
`zeline --version` and `zeline tools list` exercise your working tree.

Fork first even for a one-line change — `main` is protected, so the branch has to
live somewhere you can push to. See [Opening a pull request](#opening-a-pull-request).

## The loop

```bash
python -m unittest discover -s tests          # whole suite
python -m unittest tests.test_agent           # one module, while iterating
ruff check zeline tests                       # the same gate CI blocks on
python -m compileall -q zeline                # catches syntax errors early
```

`ruff check` here is deliberately narrow — undefined names, broken f-strings,
invalid syntax, mistaken comparisons. Those are bugs, so CI can block on them
without demanding a repo-wide reformat. Remaining style debt is reported by CI
without failing the build; see `[tool.ruff.lint]` in `pyproject.toml`.

The suite is around 1,000 tests and takes a few minutes on a laptop, longer on a
phone. While iterating, run the module you are touching and the full suite once
before pushing.

## Opening a pull request

You do not need write access to this repository, and you should not expect it:
`git push -u origin <branch>` fails with a 403 for everyone except the
maintainer. Work from your own fork instead.

```bash
gh repo fork Mftrferdinand/Zeline --clone --remote
cd Zeline
git checkout -b fix/short-description
# work, commit
git push -u origin fix/short-description
gh pr create --repo Mftrferdinand/Zeline --base main
```

`gh repo fork --remote` leaves you with `origin` pointing at your fork and
`upstream` at this repository, which is what the commands above assume. Without
`gh`, fork through the web UI and wire the remotes by hand:

```bash
git clone https://github.com/<your-username>/Zeline.git
cd Zeline
git remote add upstream https://github.com/Mftrferdinand/Zeline.git
```

Keep the fork current before starting new work, or your pull request arrives with
unrelated conflicts:

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main
```

Branch names follow the commit type: `fix/`, `feat/`, `docs/`, `ci/`, `chore/`.

Your pull request runs the full CI matrix — Linux, Windows, macOS, and the
installers. Fork pull requests get the same treatment as the maintainer's,
because the workflows trigger on `pull_request` rather than
`pull_request_target`. That means a first-time contributor sees exactly the same
gate, and no secret is exposed to a fork.

**Commit messages** use `type(scope): what changed, in the imperative`, and the
body explains *why* — what broke for a user, and why this fix rather than the
obvious alternative. Compare:

```
fix(discord): keep each connection's heartbeat on its own socket
```

against `fix: bug`. The first one tells a future reader, including you, what to
look for.

**The pull request body** should answer three questions: what was wrong, what
changed, and how it was verified. State what you could *not* verify — an
environment you had no access to, a failure you could only reproduce by
reasoning — instead of leaving a reviewer to assume it was all covered.

## What CI checks

Every pull request runs, and **all of it must be green before merge** — every row
below is a required status check, so none of them can be red and still merge:

| Job | What it proves |
| --- | --- |
| `test (3.10 … 3.13)` | the suite passes on every supported Python |
| `windows (3.10, 3.13)` | no POSIX-only assumptions (`termios`, `os.kill`, console code pages) |
| `macos` | the suite plus `install.sh` on BSD userland |
| `installer-windows` | `install.ps1` runs on a clean runner, in PowerShell 5.1 too |
| `package` | the built wheel carries no local state (`.zeline`, `.env`, `__pycache__`) |
| `lint` | the correctness ruff gate |
| `Analyze Python` / `CodeQL` | security analysis |
| `dependency-review` | new dependencies are inspected |

Windows and macOS jobs catch the majority of surprises, because Zeline is
developed on Linux/Termux. If you only ever run the suite on Linux, expect the
first CI run to teach you something.

## Where things live

| Path | What it is |
| --- | --- |
| `zeline/agent.py` | the agent loop: tool rounds, cancellation, reflection |
| `zeline/tools.py` | native tools, tool definitions, profile gating |
| `zeline/config.py` | configuration, defaults, the system prompt |
| `zeline/gateways/` | Telegram, WhatsApp, Discord, webhook |
| `zeline/skills.py` | the skill store and `manage_skill` |
| `zeline/custom_tools.py` | operator-supplied Python files as `custom_*` tools |
| `zeline/plugins.py` | hooks that audit, rewrite, or block a tool call |
| `zeline/openapi_tools.py` | a local OpenAPI 3 document as `api_*` tools |
| `docs/extending.md` | how to add your own tools without forking |
| `docs/agent-working-style-and-reliability.md` | reliability and chat-UX lessons already paid for |
| `tests/` | one module per subject, `unittest` |

Adding a tool is not a one-file change: a new native tool needs its `ToolDef` in
`zeline/tools.py`, a handler, a title in the Telegram progress renderer, and an
entry in the compaction artifact map. Grep for an existing tool name before you
declare it done.

If you want to extend Zeline for yourself rather than change it for everyone,
read `docs/extending.md` first — custom tools, plugin hooks, OpenAPI tools, and
MCP servers all work without touching this repository.

## Reporting things

- **Bugs and features:** open an issue. The templates ask for the version
  (`zeline --version`), platform, and the smallest reproduction you have.
- **Security:** do not open a public issue. Use GitHub's private vulnerability
  reporting for this repository — see [SECURITY.md](SECURITY.md).

## Releases

Releases are cut by the maintainer. A version bump touches ten files that a test
cross-checks against each other, and the tag triggers a workflow that verifies
the tag belongs to merged `main` before publishing anything. Do not include a
version bump in a feature pull request.

Whether a release also publishes to PyPI depends on a Trusted Publisher
registered in a PyPI account, which nothing in this repository can see. Check it
without cutting a release: **Actions → Release → Run workflow**. That runs
`verify-pypi-publisher`, which exchanges an OIDC token with PyPI exactly as the
publish step does and writes the verdict to the run summary — including the exact
fields to register if it is not configured. It builds nothing and never fails the
run.

Public docs may only advertise an install command that actually works.
`PyPiAvailabilityClaimTests` in `tests/test_community_docs.py` enforces this with
a single `PYPI_PUBLISHED` switch: while it is `False`, no page may hand a reader
`pip install zeline` or `uv tool install zeline`; flip it in the same commit that
lands the first successful PyPI upload and the same test then requires the READMEs
and install guide to document that route.
