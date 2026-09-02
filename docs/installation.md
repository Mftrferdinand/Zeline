# Install Zeline

```text
╭───────────────────────────────────────╮
│           Z  E  L  I  N  E            │
├───────────────────────────────────────┤
│   AGENTIC AI BY ZEROLINEAR • v0.2.8   │
╰───────────────────────────────────────╯
```

On Termux, Linux, macOS, and iSH, Zeline installs into a **private Python
environment** owned by your account. Windows uses a per-user Python package
install. Neither path needs `sudo` or Administrator access, and the POSIX
installer avoids PEP 668 conflicts on current Linux and macOS releases.

## Support matrix

| Platform | Interactive CLI | Messaging gateway | Browser automation |
|---|---:|---:|---:|
| Termux / Android | Yes | Yes, use `termux-wake-lock` | Built-in HTTP tools; browser MCP can be remote |
| Linux | Yes | Yes | Built-in HTTP tools; browser MCP supported |
| macOS | Yes | Yes | Built-in HTTP tools; browser MCP supported |
| iOS / iPadOS through iSH | Yes | Foreground only; iOS may suspend iSH | Built-in HTTP tools only |
| Windows PowerShell | Yes | Yes | Built-in HTTP tools; browser MCP supported |

**Requirements:** Python 3.10+. Git is only needed for checkout installs;
WhatsApp also needs Node.js 18+ and npm.

One line on every POSIX platform — Termux, Linux, macOS, and iSH:

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
```

Then `zeline setup`.

### From PyPI

Zeline is also published to PyPI, so if you already have Python tooling you can
skip the installer entirely:

```bash
uv tool install zeline
```

`uv tool install` is preferred over `pip install zeline` because it puts Zeline in
its own isolated environment with the `zeline` command on your PATH, which is what
the installer script arranges by hand. Use `pip install zeline` only inside an
environment you manage yourself.

The installer remains the recommended route for a fresh machine: it provisions
Python, creates the private environment, and verifies the release wheel against
`SHA256SUMS` without assuming you already have `uv` or `pip`.

### Why there is no checksum step to copy

Release assets are immutable tag assets, and the installer verifies the versioned
wheel against `SHA256SUMS` itself — refusing to install on a mismatch, a
malformed digest, a missing entry, or a non-HTTPS URL. Pasting a hand-written
SHA-256 check for `install.sh` added nothing: it compared the installer against a
manifest fetched over the same connection from the same release, so anyone able
to tamper with one could tamper with both. It cost every reader fifteen lines and
bought no security.

What *does* add a separate guarantee is build provenance, which is signed by
GitHub rather than served alongside the file. If you want that assurance, verify
it with GitHub CLI before running the installer:

```bash
gh attestation verify install.sh --repo Mftrferdinand/Zeline
```

Attestations cover the wheel, source archive, both installers, and the checksum
manifest.

## Termux

Install Termux from F-Droid or GitHub (the Play Store build is obsolete), then:

```bash
pkg update -y
pkg install python curl -y
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
zeline setup
```

For a gateway that must stay alive while the screen is off:

```bash
termux-wake-lock
zeline gateway start
```

## Linux

Debian / Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-venv curl -y
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
zeline setup
```

Fedora:

```bash
sudo dnf install python3 curl -y
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
zeline setup
```

Arch Linux:

```bash
sudo pacman -S --needed python curl
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
zeline setup
```

Zeline itself never uses `sudo`; the commands above only install OS packages.

## macOS

Install Apple command-line tools and Homebrew Python if needed:

```bash
xcode-select --install
brew install python
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
zeline setup
```

The private Zeline environment avoids Homebrew's
`externally-managed-environment` / PEP 668 restriction. Both Apple Silicon and
Intel Macs are supported by the same command.

## iOS / iPadOS (iSH)

Apple does not allow Zeline to run as a native unrestricted background process.
Use [iSH](https://ish.app/), an Alpine Linux shell for iOS/iPadOS:

```sh
apk update
apk add bash curl python3 py3-pip
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
zeline setup
```

The CLI and normal HTTP integrations work. Keep iSH in the foreground for a
messaging gateway: iOS can suspend it after the app leaves the foreground.
Local Chromium/Playwright is not supported in iSH; use Zeline's HTTP browser or
a remote browser integration instead.

## Windows PowerShell

Open **PowerShell** or **Windows Terminal**, not Command Prompt. Administrator
rights are not required:

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.ps1 -OutFile install.ps1; .\install.ps1
```

Then `zeline setup`. `install.ps1` verifies the versioned wheel with
`Get-FileHash -Algorithm SHA256` against `SHA256SUMS` before installing it, and
rejects a malformed digest with `-notmatch '^[0-9a-f]{64}$'`.

If `python` opens Microsoft Store, install Python 3.10+ from
[python.org](https://www.python.org/downloads/windows/) and tick **Add
python.exe to PATH**. The installer detects and rejects the Microsoft Store
alias stub.

If `zeline` is not recognized immediately, open a new terminal. The installer
also prints the exact selected-interpreter command (for example `python -m
zeline.cli` or `py -3 -m zeline.cli`) when it leaves PATH unchanged.

## Install from a checkout

POSIX platforms:

```bash
git clone https://github.com/Mftrferdinand/Zeline.git
cd Zeline
bash install.sh --source .
```

Windows PowerShell:

```powershell
git clone https://github.com/Mftrferdinand/Zeline.git
cd Zeline
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Source .
```

## Preview platform requirements only

The POSIX installer can explain what it would use without installing anything:

```bash
bash install.sh --platform-info
bash install.sh --platform-info --platform termux
bash install.sh --platform-info --platform linux
bash install.sh --platform-info --platform macos
bash install.sh --platform-info --platform ios-ish
```

## Configure Zeline

```bash
zeline setup
zeline model
zeline tools list
zeline mcp list
zeline doctor
```

Tool access is explicit:

```bash
zeline tools profile safe
zeline tools profile workspace
zeline tools profile full
zeline tools workspace ~/projects
zeline tools disable run_shell
zeline tools enable run_shell
```

`full` on a messaging gateway is refused until that gateway has an owner
allowlist. This prevents a public bot from becoming a remote shell.

### Telegram owner setup

During `zeline setup` the Telegram step asks for an **owner chat ID** — your
numeric Telegram account ID (message `@userinfobot` to get it; this is not your
`@username`). What you enter decides the bot's trust level:

- **Owner chat ID given** → the bot has a single trusted user, so it starts with
  the **full toolset** (native tools + MCP + image analysis) out of the box. The
  ID becomes the sole allowlist entry, `tool_profile` is set to `full`, and the
  remote-code-execution acknowledgement is recorded automatically — no extra
  `zeline tools` command needed.
- **Left empty** → the bot stays **public with safe tools only** (memory + web).
  Re-run `zeline setup` and supply an owner chat ID later to unlock full tools.

The full profile is safe here precisely because the allowlist is your single
owner: there is no other user the runtime would execute tools on behalf of.

## Update

One command, every platform — Termux, Linux, macOS, iSH, and Windows
PowerShell:

```bash
zeline update
```

`zeline upgrade` is an alias. The updater picks the right path automatically:

| Where Zeline runs from | What `zeline update` does |
|---|---|
| Release install (POSIX) | Downloads `install.sh` + `SHA256SUMS` from the latest release, verifies the SHA-256, runs it |
| Release install (Windows) | Downloads `install.ps1` + `SHA256SUMS`, verifies the SHA-256, runs it through PowerShell |
| Git checkout | Rebuilds and reinstalls your local source with the installer's `--source` / `-Source` mode |

Downloads are HTTPS-only and the installer is refused if its checksum does not
match the release manifest. The private environment and `zeline` command are
updated in place; your configuration, sessions, memory, and private skills under
`~/.zeline` remain untouched.

After updating, reload any running gateway:

```bash
zeline gateway restart
```

Running the platform installer again by hand still works and is equivalent.

### In-flight work is not cut off

`zeline update` and `zeline gateway restart` **drain** before stopping: the
gateway stops accepting new messages, lets any agent turn already running
finish, and only then exits. A build, install, or long analysis in progress is
not killed mid-way, and a message that arrives during the drain is answered
with a short "finishing current work before restarting" notice instead of being
dropped.

If a gateway was running, `zeline update` brings it back automatically on the
updated code — including when the update itself fails, so you are never left
with a stopped gateway.

Tune or disable the wait with `agent.restart_drain_timeout` (seconds, default
`30`; `0` restores the old immediate stop). If the drain does not finish in
time, Zeline escalates to a forced stop **and says so** rather than reporting a
clean restart.

### Version and update from Telegram

A phone-only install never has to reach for a shell:

| Command | What it does |
|---|---|
| `/version` | Installed build vs. the latest release, plus whether an update is already running |
| `/update` | Runs the update and reports the outcome in the chat |

`/version` is read-only and safe for anyone on the allowlist. `/update` is
**owner-only** — it replaces the installed package and restarts the gateway for
every user of that bot, so only the first chat ID in the allowlist may run it,
and a bot with no allowlist has it disabled entirely.

The update cannot run inside the gateway it is updating: `zeline update` drains
that gateway and, if the drain times out, escalates to a forced stop on its
process group. So `/update` spawns a **detached** updater in its own session,
which then drives the update and posts progress directly through the Bot API —
the gateway is deliberately down for most of it. Expect roughly a minute of
unreachability. The outcome message reports the version read back from a fresh
interpreter, so it can never claim success at the old number, and a lock file
makes two concurrent updates impossible. The full transcript is written to
`~/.zeline/logs/self-update.log`.

From a git checkout `/update` deliberately refuses and prints the two commands to
run instead, because installing an uncommitted working tree from a chat message
is not what "update" should mean.

On Windows there is no `SIGUSR1`, so restart uses the standard managed stop
path.

## Data and uninstall

| Item | Default location |
|---|---|
| Runtime / private Python | `~/.local/share/zeline` |
| CLI command | `~/.local/bin/zeline` (or platform command directory) |
| Config and owner data | `~/.zeline` |

Remove the program but keep your data:

```bash
rm -rf ~/.local/share/zeline ~/.local/bin/zeline
```

Remove everything, including local configuration and sessions:

```bash
rm -rf ~/.local/share/zeline ~/.local/bin/zeline ~/.zeline
```

On Windows, remove the installed `zeline.exe`/package with the Python selected
by the installer, and delete `%USERPROFILE%\.zeline` only if you also want to
delete local data.

## Verify and troubleshoot

```bash
zeline --version
zeline doctor
zeline tools list
zeline gateway list
```

- **`zeline: command not found`** — open a new terminal or add
  `~/.local/bin` to `PATH`.
- **Linux says `No module named venv`** — install `python3-venv`.
- **macOS uses an old Apple Python** — run `brew install python`, then rerun.
- **iSH gateway stops** — iOS suspended the app; keep iSH foreground or run the
  gateway on Termux/Linux/macOS/Windows.
- **Windows box characters look wrong** — use Windows Terminal or PowerShell 7.

Repository and issue tracker:
[github.com/Mftrferdinand/Zeline](https://github.com/Mftrferdinand/Zeline)
