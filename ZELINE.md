# Zeline — project conventions

Conventions for an AI agent working inside this repository. Zeline reads the
first file it finds from `ZELINE.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`
and loads it into the system prompt, so what is written here reaches every turn.
Keep it short, factual, and specific to *this* repo: it is project context, not
permission, and it cannot widen a tool profile or waive a confirmation.

## What this project is

Zeline is an open-source agentic AI framework: an agent loop with tool calling,
skills, persistent memory, and gateway adapters that expose the same agent
through a CLI or a chat platform. Pure Python, standard library first — the only
runtime dependencies are `requests`, `PyYAML`, and `pypdf`. Python 3.10+.

## Layout

- `zeline/` — the package. `agent.py` is the loop (tool rounds, cancellation,
  reflection), `tools.py` the native tools and profile gating, `config.py` the
  configuration and system prompt, `gateways/` the platform adapters
  (Telegram, WhatsApp, Discord, webhook).
- `zeline/skills/` — bundled skills shipped as package data.
- `zeline/zenith_tools/` — scripts a skill can invoke.
- `tests/` — one `unittest` module per subject.
- `docs/` — installation, extending, translated READMEs, working-style notes.
- `.github/workflows/` — the CI matrix.

## Commands

```bash
pip install -e ".[dev]"                # -e so the CLI you run is the code you edit
python -m unittest discover -s tests   # whole suite, ~1,000 tests
python -m unittest tests.test_agent    # one module, while iterating
ruff check zeline tests                # the exact gate CI blocks on
```

Run the full suite before reporting work as done. `ruff check` here is narrow by
design (undefined names, broken f-strings, invalid syntax, bad comparisons) — do
not widen it or reformat the repo to satisfy a rule CI does not enforce.

## Conventions

- Read a file before editing it, and edit the specific lines. Do not regenerate
  a module from scratch or overwrite it with an older copy.
- Match the style of the surrounding code. Adding a dependency or a formatter is
  a separate, asked-for change.
- Comments and docstrings explain *why* a decision was made — the constraint, the
  failure it prevents — not what the next line does. Both English and Indonesian
  appear in this codebase; follow whichever the file already uses.
- Tests are behavioural: name what breaks for a user. A new native tool is not
  one file — it needs its `ToolDef`, a handler, a title in the Telegram progress
  renderer, and an entry in the compaction artifact map.
- Commits use `type(scope): what changed, in the imperative`; the body says why.
  Branches follow the type: `fix/`, `feat/`, `docs/`, `ci/`, `chore/`.

## Do not

- Commit secrets, tokens, `.env`, or anything under `~/.zeline/`.
- Put a personal identifier in shipped code: no private hostname, IP, phone
  number, account handle, or absolute home path. Tests in
  `tests/test_public_package_sanitization.py` enforce this, and a bundled skill
  is as public as the README.
- Push to `main` — it is protected. Branch, open a pull request, wait for the
  full matrix.
- Claim an action succeeded before a tool result confirms it. Report the blocker
  instead, and never invent output to stand in for a run that did not happen.

More detail: `CONTRIBUTING.md` for the pull-request process, `docs/extending.md`
for extending Zeline without forking it, and
`docs/agent-working-style-and-reliability.md` for reliability lessons this
project already paid for.
