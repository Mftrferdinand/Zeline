# Integration and authorization

## No automatic trading

The supplied distribution is a standalone Node.js engine, not an existing Zeline tool. It combines read-only chain discovery with state-changing wallet commands, an upstream Telegram poller, a file updater and long-running loops. Installing a bundled skill must not start any of these.

Zeline exposes offline inspection, bounded dependency checks, and private workspace preparation. The original engine remains available as source for inspection. Prepared workspaces contain `watch.upstream.mjs` as a reference and a `watch.mjs` entrypoint that exits without loading it. There is no live-enable switch in the integration. This is a deliberate installation boundary, not proof that the upstream engine is safe or that a live strategy has been implemented.

## Actual trading

For a specific mint, sale, swap, approval/revocation, external signature or payment: use the separate Binance Agentic Wallet skill, verify connection/settings, parse the relevant preview and risks, and ask the owner to approve the exact transaction before execution. A bulk installation request does not authorize transactions. Automatic strategy execution requires a separately reviewed transaction-authorization design; do not bypass this boundary by invoking the original engine directly.

## Local runtime and files

Node.js 20 or newer is recommended for the upstream code's built-in fetch/AbortSignal support. The local `baw` CLI must support the actual commands used; a version/help check is not a connected-wallet check. Only the Python standard library is required by this integration's offline wrapper.

Prepare a new owner-private directory outside the source repository and package assets. Existing paths, symlinks and package-internal paths must be rejected. Do not import wallet session files or bot credentials automatically. A successful preparation copies templates and reference code; it does not fill in real accounts, start scanning, create tasks, or establish live capability.

## Telegram and updates

The upstream engine uses Telegram `getUpdates`, which would compete with Zeline's existing gateway if the same bot token were reused. No polling or notification bot is started by this integration. Do not ask for a new bot token merely to finish installation.

The upstream updater can replace its own script. The bundled distribution is pinned to the supplied archive/file hashes; upstream update code must not run in the package directory. Preserve local provenance, review a new archive separately, and keep rollback copies before an approved upgrade.

## Scope and provenance

The MIT license permits redistribution with its copyright and permission notice intact. Retain both upstream and English-fork attribution. Archive hashes establish the supplied bytes, not independent authenticity or a smart-contract security audit. No public push, release, gateway restart, runtime-monitor edit, login or on-chain operation is part of this installation.
