# SkillForge: measured skill improvement

SkillForge is the framework's local improvement engine (`zeline/skillforge.py`).
It reuses the installed agent and provider, works on the Telegram/CLI framework,
and does not depend on Zeline App, a hosted service, or a second model registry.
The historical `zenith_tools/scripts/skill_forge.py` entry point delegates to it.

## What is connected

- Normal agent turns observe successful skill loads (including auto-injected
  procedures) and subsequent real executor results. Parallel actions chosen in
  the same round as a skill load cannot be credited to that skill: the model had
  not read it yet.
- Native `exit=` shell/Python failures, HTTP error statuses and terminal process
  results are parsed.
  Launch acknowledgements and running jobs are unknown, not successful checks.
  A turn with unfinished observed jobs cannot earn a win from an unrelated read.
  Across turns there is no implicit job-to-skill attribution: a terminal poll must
  occur in an eligible skill-backed turn to contribute a completion observation.
- A load, a generated answer, an internal reflection, or a claimed successful task
  is not by itself a successful observation. Unknown and cancelled turns do not
  raise effectiveness. Multi-skill turns are marked unknown because attribution
  is ambiguous. A failure anywhere after a single eligible skill load is a
  conservative failure observation; provider interruptions remain unknown.
- The score uses a 90-day observation window, a 30-day recency half-life, and a
  Laplace prior. Five effective observations are required before low effectiveness
  can trigger review. The confidence interval is approximate and makes uncertainty
  visible; this is an observed operational signal, **not causal proof that the
  skill is correct or a guarantee that the requested task was completed**.
- Skill age (90 days by default) requests review; it does not prove obsolescence.
  Missing telemetry stays `null`, not a fabricated 100% score.
- Internal `reflect()` skill create/patch calls stage proposals instead of writing
  active skills. Destructive, script, network, and custom-tool dispatch is blocked
  during that internal phase. Operator plugin denial/redaction hooks and disabled
  tools still apply. Proposal-store errors are returned without aborting the main
  session. Ordinary operator-requested `manage_skill` behavior is unchanged.
- `/skillforge` in Telegram is owner-only and shows observed effectiveness, review
  reasons, and pending proposals. It does not approve or apply anything.

Telemetry is local SQLite under `$ZELINE_HOME/skillforge/` (default
`~/.zeline/skillforge/`). Identity hashes, random turn IDs, skill names, revision
hashes, observed outcomes, counts and timestamps are stored. Prompts, tool
arguments, raw outputs and provider credentials are not stored as telemetry.
Proposals necessarily store their candidate Markdown; do not include credentials.
Telemetry is best-effort and must never break an agent turn.

## Operator workflow

Inspection is offline by default:

```bash
zeline skillforge scan --limit 20
zeline skillforge score my-skill
zeline skillforge list
zeline skillforge show forge_<proposal-id>
```

Stage a candidate without editing the live skill:

```bash
zeline skillforge propose my-skill ./candidate-SKILL.md --reason "Correct the verified failure"
```

A candidate must have matching `name`, nonempty `description`, YAML frontmatter,
a heading, and a procedure. The same source and candidate revision deduplicate.
Candidate names cannot be paths or safety-critical names.

Run a bounded stale -> draft -> test cycle:

```bash
zeline skillforge cycle --limit 5          # inspection only; no model call
zeline skillforge cycle --limit 2 --draft  # explicitly permits configured-provider calls
```

The cycle skips an already pending revision, never approves or applies its work,
and does not schedule itself. No periodic job or background API spend is enabled
by default. The Python API accepts injected `drafter` and `evaluator` functions
for deterministic automation or an operator-owned scheduler.

## Testing is not approval

```bash
zeline skillforge test forge_<proposal-id>
```

This stages a disposable copy, validates Markdown/frontmatter and syntax-parses
Python companions. It **does not execute proposed scripts**. Structural success
alone is insufficient for approval.

A trusted operator evaluator supplies the workflow's actual acceptance contract:

```python
# evaluator.py -- operator-owned code, never copied blindly from a proposal
from pathlib import Path


def evaluate(stage: Path) -> dict:
    # Exercise an isolated fixture for this workflow here. Assert the expected
    # artifact/result, and report only cases that actually ran. Do not treat a
    # successful parse or a model's self-report as behavioral proof.
    from my_fixture_suite import verify_workflow
    verify_workflow(stage)
    return {"passed": True, "cases": 1}
```

```bash
zeline skillforge test forge_<proposal-id> --evaluator ./evaluator.py --trust-evaluator
```

An evaluator runs with the operator's Python permissions: the disposable directory
is **not a security sandbox**. `--trust-evaluator` is an explicit local-code trust
acknowledgement. Evaluator failures, zero cases, or candidate/companion changes
invalidate behavioral success. The CLI returns a nonzero exit status on behavioral
failure when an evaluator was requested; structural-only inspection is explicitly
separate and cannot approve a skill. Do not execute commands supplied by a generated
proposal as tests.

Review and application are separate, explicit actions:

```bash
zeline skillforge review forge_<proposal-id> --approve --reviewer operator
# Or reject without changing the active skill:
zeline skillforge review forge_<proposal-id> --reject --reviewer operator
zeline skillforge apply forge_<proposal-id> --confirm
```

Destructive commands, remote-code patterns, safety changes, and funds/credential
references are flagged for review. Approval requires acknowledging each reported
risk using `--ack-risk <risk-id>`. Pattern scanning is a warning mechanism, not a
complete security assessment; the operator must review the full source/candidate.
The APIs are not a privilege barrier against an already unrestricted local shell.

Approval is bound to the exact candidate, source/companion revision and inspected
test evidence. A concurrent retest invalidates a stale review decision. A later
operator edit invalidates the proposal rather than being overwritten. Application
creates a mandatory backup under `skillforge/backups/<proposal-id>/`, publishes
with atomic replacement, preserves companions, and writes only the private
skill/override. Packaged skills, framework source, safety files, and integrity
locks are not changed by the cycle. Proposal transitions are recorded in a local
audit table. This does not claim a cross-process lock against unrelated manual
editors or crash-atomic transactions spanning SQLite and the filesystem.

## Verification and rollout

```bash
python -m unittest discover -s tests -p test_skillforge.py -v
python -m unittest discover -s tests -p test_agent.py -q
python -m unittest discover -s tests -p test_public_core.py -q
```

All tests use temporary state and deterministic providers/fixtures. The runtime
integration test executes real skill/file tools but fixtures the model response;
it is not evidence about every live model's quality. Apply/review tests operate
only on disposable skills. A checkout change does not change a separately pip-
installed bot: install verified bytes deliberately, and do not restart a running
gateway without the operator's approval.
