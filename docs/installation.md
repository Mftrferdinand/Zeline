# Install Zeline

```text
╭───────────────────────────────────────╮
│           Z  E  L  I  N  E            │
├───────────────────────────────────────┤
│   AGENTIC AI BY ZEROLINEAR • v0.2.0   │
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
WhatsApp also needs Node.js 18+ and npm. Release installers and packages are
immutable tag assets. Verify them against `SHA256SUMS` before execution:

```bash
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
```

The verifier above uses Python standard-library SHA-256 on every POSIX OS.
Windows uses `Get-FileHash -Algorithm SHA256`; the verified installer then verifies the
versioned wheel again before installing it. The GitHub release also carries
build-provenance attestations for the wheel, source archive, installers, and
checksum manifest.
If GitHub CLI is installed, independently verify that provenance with:

```bash
gh attestation verify zeline-0.2.0-py3-none-any.whl --repo Mftrferdinand/Zerolinear
```

## Termux

Install Termux from F-Droid or GitHub (the Play Store build is obsolete), then:

```bash
pkg update -y
pkg install python curl -y
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
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
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
zeline setup
```

Fedora:

```bash
sudo dnf install python3 curl -y
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
zeline setup
```

Arch Linux:

```bash
sudo pacman -S --needed python curl
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
zeline setup
```

Zeline itself never uses `sudo`; the commands above only install OS packages.

## macOS

Install Apple command-line tools and Homebrew Python if needed:

```bash
xcode-select --install
brew install python
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
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
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(x.split()[0] for x in lines if x.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
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
$base = 'https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.0'
Invoke-WebRequest "$base/install.ps1" -OutFile install.ps1
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS
$expected = ((Get-Content SHA256SUMS | Where-Object { $_ -match ' install.ps1$' }) -split '\s+')[0]
if ((Get-FileHash install.ps1 -Algorithm SHA256).Hash.ToLower() -ne $expected.ToLower()) { throw 'checksum mismatch' }
.\install.ps1
zeline setup
```

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
git clone https://github.com/Mftrferdinand/Zerolinear.git
cd Zerolinear
bash install.sh --source .
```

Windows PowerShell:

```powershell
git clone https://github.com/Mftrferdinand/Zerolinear.git
cd Zerolinear
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

## Update

Run the same installer again. The private environment and `zeline` command are
updated in place; your configuration, sessions, memory, and private skills under
`~/.zeline` remain untouched.

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
[github.com/Mftrferdinand/Zerolinear](https://github.com/Mftrferdinand/Zerolinear)
