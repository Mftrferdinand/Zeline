# Reliability component corpus

Run `python -m pytest -q tests/test_reliability_corpus.py` from the source tree.
The subprocess entry point is `tests/reliability_probe.py`. Each case creates
fresh HOME/ZELINE_HOME before importing runtime configuration, excludes provider
credentials from inherited environment, uses generated benign fixtures, and
forbids socket connects. It does not start a live provider or installed gateway.
This guard is not an OS sandbox.

Optional `WAVE1_REPORT_DIR=/approved/output/path` writes bounded synthetic case
receipts with provenance and selected verification-time source hashes. These are
component contract records, NOT measured model AgentRuns or competitive scores.
Git HEAD identifies the base checkout; unavailable runtime build SHA stays null.

## Cases

- Distinct punctuation variants and distinct long identities must not append to
  the same compaction transcript. Same identity/day still appends to one file.
- Legacy sanitized-only transcripts are neither merged nor migrated on write.
- Workspace-profile read_file permits the caller's new owner-keyed archive,
  rejects foreign/ambiguous legacy archives, and resolves symlinks before the
  ownership check. A broad workspace root does not bypass that read check.
  Workspace-profile search_files applies the same owner filter before pagination,
  so foreign and legacy archives are not indexed. Full profile retains trusted
  archive access, including legacy files. POSIX-only generated legacy filenames
  containing colons, digits and a newline are also checked through both search
  targets; unsupported filename capabilities are reported as skips.

`tests/test_search_owner_filter.py` independently exercises both native ripgrep
and Python fallback filtering. It checks filtering before offset/page limits,
fail-closed policy exceptions, exact invalid-UTF-8 filenames on POSIX, and native
records split across single-byte reads. These tests do not depend on display
text being a parseable filename format.

- Memory, session/checkpoint records and job views/cancel/ack/dependency checks
  are scoped to the requested identity for the tested identities.
- Two real `_trim_history` passes preserve the system message, bounded digest,
  artifact reference and completed tool evidence without executing tools.
- Real Worker child processes exit via `os._exit` before an effect, after the
  effect/before result commit, and after explicit durable result commit.
- Real source-agent turns crash at pre-dispatch checkpoint, after a benign native
  file write/before result checkpoint, and after result checkpoint. Fresh
  SessionPersistence loads and a controlled resumed turn must not replay tools.

## Recovery semantics and limitations

Default non-retry-safe jobs with expired leases become `failed` with the existing
"outcome uncertain" warning. The corpus verifies this actual public state; it
DOES NOT claim a new product `outcome_unknown` enum was implemented. Such jobs
are not automatically reclaimed. `retry_safe=true` remains a separate explicit
policy and is not an exactly-once guarantee.

Checkpoint records distinguish recorded tool results from missing outcomes and
carry an UNKNOWN warning on interrupted turns. Loading never executes tools.
The resumed provider is controlled to respect that warning; this is not proof
that every real model will avoid repeating a mutation. External effects require
state verification and/or endpoint-specific idempotency.

Process exits are real but do not simulate power loss, a corrupt filesystem or
network-side transaction semantics. Compaction is bounded extraction, not proof
that every old instruction survives. Logical identity-scoped storage is not
hostile multi-tenant filesystem isolation: identities sharing a process/OS user
are not security principals. The workspace read_file archive exception now has
  an owner check, and workspace search_files applies the same archive filter;
  full profile, direct trusted helpers, other tools and OS filesystem access are
  not turned into an all-tool tenant ACL. Legacy archive
  references may require trusted full-profile/manual inspection; no automatic
  owner assignment is made.

## Archive filename compatibility

New transcript names combine a readable sanitized prefix, a SHA-256-derived full
identity key and UTC date. The key prevents the demonstrated sanitization and
prefix-truncation collisions. Existing filename references remain valid, and
normal archive retention is unchanged. Historical sanitized-only files may have
ambiguous ownership or already mixed content; they cannot be safely auto-split
or relabeled. No automatic migration is performed.
