## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Highlights

- **Canonical high-agency identity shipped to every install** — `SOUL.md` is packaged in the wheel and source archive, loaded into every agent's system prompt, and startup fails clearly if it is missing or empty. The public doctrine is now execution-first: persist through recoverable failures, keep long work visibly progressing, honor stop/cancel absolutely, and verify real outcomes—without private personas, operator PII, infrastructure, or safeguard-bypass clauses.
- **Long-running commands no longer fail at 60 seconds** — `run_shell` and `execute_code` accept a `timeout` (default 60s, up to 900s), so real `pip`/`npm`/`apt` installs, builds, and test suites finish instead of being reported as failures.
- **Background processes with real lifecycle control** — `run_shell(background=true)` starts servers, watchers, and long builds detached and returns a job id; the new `process_control` tool lists, polls incrementally, tails logs, and kills the whole process group. Finished jobs stay readable for 30 minutes.
- **Forceful `/stop` that preserves the session** — foreground shell/code processes are tracked per identity and terminated with their children; OpenAI and Anthropic streams observe cancellation per chunk; incomplete tool-call tails are repaired so the next message remains valid. Long Telegram turns display elapsed time, step count, remaining budget, and a `/stop` hint instead of going silent.
- **Termux gateways survive Android power management** — gateway polling automatically requests the app-wide Termux CPU wake lock, managed `gateway start` is recommended for daily Android use, and stopping an unmanaged foreground gateway never signals a shared shell process group. This prevents Zeline shutdown from taking unrelated Termux agents with it.
- **Shell access remains owner-only** — background shell and `process_control` are exposed only to the `full` profile, so public gateways still cannot reach a shell.
- **Sanitized public skill package** — removes personal account examples, payment identifiers, infrastructure details, local router state, and session-specific deployment notes from bundled skills.
- **Normalized Zenith references** — fixes duplicated `zeline-zenith-` prefixes across the bundled compatibility corpus without renaming stable skill IDs.
- **Reliable public documentation** — replaces unresolved website links with the repository documentation that is available now.
- **Future-proof release validation** — artifact checks now derive the package version from the verified release tag instead of hardcoding a version.
- **Security reporting enabled** — vulnerabilities can be reported privately through GitHub Security Advisories.
- Versioned, checksum-verified installers for Termux, Linux, macOS, iSH, and Windows PowerShell.
- Immutable release artifacts with build provenance.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.5/docs/installation.md) for checksum-verified commands on every supported platform.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
