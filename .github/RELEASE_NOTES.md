# Zeline v0.4.7

This release integrates canonical file-backed memory, the native code-tools
bridge, and runtime continuity and progress fixes while preserving existing
operator configuration and the verified-artifact publishing contract.

## Highlights

- Canonical, identity-scoped `MEMORY.md` and `USER.md` documents, with guarded
  migration, atomic writes and read-back evidence. SQLite remains a rebuildable
  fact index and retrieval/evidence store rather than the source of truth.
- Native file, patch and shell tools callable from Python `execute_code`
  snippets through the temporary `zeline_tools` bridge, retaining the original
  executor's permissions, checkpoints, audit, cancellation and budgets.
- Provider-wait recovery and progress watchdogs distinguish slow work from
  abandoned activity. Safe restart/update defers when an active gateway cannot
  be drained, instead of forcing a stop or replacing code beneath active work.
- Reply-context continuity keeps follow-ups associated with their originating
  task and message rather than guessing from the latest conversation alone.
- One Telegram activity feed shows actual commands and code previews. Only exact
  reruns of the same tool arguments receive `(x2)`, `(x3)` repeat badges;
  different snippets and heartbeat redraws do not inflate a generic counter.
- Runtime agent-summary refresh is independent of opening the Agents panel,
  keeping the main-agent and subagent summary current in the overview.

## Verification and publication

Focused source tests, integrated CI and built-artifact verification are distinct
checks; these notes do not claim a completed full-suite run or a live gateway
upgrade. Installation and activation remain separate from source preparation.

The tag-triggered workflow verifies merged-main ancestry, tag/package version,
archive metadata and bundled-file parity. PyPI uses Trusted Publishing (OIDC),
not a long-lived API token in the repository. Publication status is reported by the
release workflow; these notes do not imply PyPI publication before it completes.
The workflow publishes the same verified wheel and sdist, without rebuilding
separate PyPI artifacts. Installer assets stay pinned to the immutable release
tag and verify the matching wheel against `SHA256SUMS` before installation.

## Install or upgrade

```sh
python -m pip install --upgrade zeline==0.4.7
```

```sh
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.7/install.sh && bash install.sh
```

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.7/install.ps1 -OutFile install.ps1; .\install.ps1
```

## Assets

- `zeline-0.4.7-py3-none-any.whl`
- `zeline-0.4.7.tar.gz`
- `install.sh`, `install.ps1`, and `SHA256SUMS`

[Installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.4.7/docs/installation.md)
[Changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.4.7/CHANGELOG.md)

[File-backed memory](https://github.com/Mftrferdinand/Zeline/blob/v0.4.7/docs/file-backed-memory.md)
[Code tools](https://github.com/Mftrferdinand/Zeline/blob/v0.4.7/docs/code-tools.md)
