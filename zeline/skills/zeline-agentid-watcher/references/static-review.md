# Static installation review: supplied English edition 2.6.6

This is a scoped static review for dormant packaging. It is not a complete security audit, on-chain simulation, endorsement of contract safety, or proof that live trading works. Line numbers below refer to the pinned `assets/upstream/watch.mjs` file.

## Material installation hazards

1. **Importing/running the engine activates autonomous wallet work.** `main()` is called at line 1619. The loop at 1583-1615 starts chain scanning, paid mint processing, holdings/sale checks, approval revocation, login checks and Telegram polling. Installation must not import or launch it. Preview success does not make arbitrary contracts safe or authorize an on-chain transaction.
2. **Shell invocation.** The normal wallet adapter uses `execFile('baw', ..., {shell: true})` at line 152; `login.mjs` uses the same pattern. Arguments can include dynamically discovered data. This is not the shell-disabled execution boundary used by Zeline's offline helper. Do not pass untrusted strings to those paths or assume the upstream CLI adapter has been hardened.
3. **Telegram poller and identity scope.** `tgCmdLoop` at 1556-1578 consumes bot updates and dispatches sells and updates. The filter at 1564 compares chat ID, not an independent owner user ID, so a configured group is not an owner-only authorization boundary. Reusing Zeline's bot would compete for updates. The integration starts no poller and imports no bot token.
4. **Self-replacement updater.** `doUpdate` at 1458-1477 fetches code and its hash from the same remote repository, then writes its own script. Substring/hostname checks are not a code-signing trust boundary. Update commands and UPDATE-file triggers exist at 1574 and 1589. The integration pins the supplied files and never invokes updater code.
5. **Mutable state and background load.** Upstream configuration, lock, logs and state are relative to the engine directory. A PID-file check with suppressed errors is not an atomic cross-process lock. Scanning has multiple concurrent workers and continuously scheduled RPC tasks; installing it does not establish mobile background reliability or justify starting a permanent scanner.

## Verified without activating the engine

- Twelve files were present in the supplied archive; archive paths and file types were checked before extraction.
- The supplied `version.json` watch hash matches the supplied `watch.mjs` bytes. This establishes archive-internal consistency, not independently authenticated origin.
- Node syntax checks passed for the three `.mjs` files.
- The original MIT license, original-project attribution and English-fork attribution are preserved.
- The Zeline helper implements offline integrity inspection, bounded local dependency checks and private inactive workspace preparation only.
- No external executable from the supplied distribution, wallet mutation, Telegram call, upstream update or installer has been executed by the integration.

## Credential handling

The engine delegates wallet commands to `baw` rather than requiring a seed/private key in its example configuration. This does not prove absence of all indirect credential risks. The setup wizard writes bot configuration to a file and login prints a pairing link/code; neither is run automatically. Keep any future configuration private and outside source/package paths, and never print or commit credentials. Review CLI sessions, proxy handling and transaction approval separately before any authorized live work.
