# Zeline installer for Windows PowerShell.
#
# Download this v0.2.4 release asset and SHA256SUMS, verify with Get-FileHash,
# then run (PowerShell, no admin needed):
#   .\install.ps1
#
# Or from a local clone:
#   git clone https://github.com/Mftrferdinand/Zeline.git
#   cd Zeline
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Source .
#
# Switches (useful for CI / unattended installs):
#   -AddToPath      add the user Scripts dir to PATH without prompting
#   -NoPathUpdate   never touch PATH and never prompt
#   -PlatformInfo   show Windows requirements without installing
#   -Source PATH    explicitly build and install a local checkout
#
# Optional environment variables:
#   $env:ZELINE_PYTHON       = "python"       # Python executable to use


#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$AddToPath,
    [switch]$NoPathUpdate,
    [switch]$PlatformInfo,
    [string]$Source
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Version     = '0.2.4'
$ReleaseRef  = 'v0.2.4'
$ReleaseBase = "https://github.com/Mftrferdinand/Zeline/releases/download/$ReleaseRef"
$WheelName   = "zeline-$Version-py3-none-any.whl"

# ---------------------------------------------------------------- utilities

function Write-Step   { param([string]$Text) Write-Host "==> $Text" }
function Write-Detail { param([string]$Text) Write-Host "    $Text" }
function Write-Fail   { param([string]$Text) Write-Host "[x] $Text" -ForegroundColor Red }
function Write-Warn   { param([string]$Text) Write-Host "[!] $Text" -ForegroundColor Yellow }

function Show-Banner {
    $title    = 'Z  E  L  I  N  E'
    # Keep the .ps1 source ASCII-safe: Windows PowerShell 5.1 treats UTF-8
    # without BOM as the legacy ANSI code page. Build box glyphs at runtime.
    $bullet   = [char]0x2022
    $subtitle = "AGENTIC AI BY ZEROLINEAR $bullet v$Version"
    $inner    = 39
    $tl = [char]0x256D; $tr = [char]0x256E
    $ml = [char]0x251C; $mr = [char]0x2524
    $bl = [char]0x2570; $br = [char]0x256F
    $v  = [char]0x2502; $h  = [char]0x2500

    function Format-Centered {
        param([string]$Text, [int]$Width)
        $pad  = [math]::Floor(($Width - $Text.Length) / 2)
        $left = ' ' * $pad
        $right = ' ' * ($Width - $Text.Length - $pad)
        return "$left$Text$right"
    }

    $t = Format-Centered -Text $title    -Width $inner
    $s = Format-Centered -Text $subtitle -Width $inner
    # Windows PowerShell 5.1 inherits a legacy code page. Switch output to UTF-8
    # so the same boxed ZELINE identity used on Termux/macOS/Linux survives.
    try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
    Write-Host ''
    Write-Host ([string]$tl + ([string]$h * $inner) + [string]$tr) -ForegroundColor DarkBlue
    Write-Host ([string]$v  + $t + [string]$v)                       -ForegroundColor White
    Write-Host ([string]$ml + ([string]$h * $inner) + [string]$mr) -ForegroundColor DarkBlue
    Write-Host ([string]$v  + $s + [string]$v)                       -ForegroundColor Blue
    Write-Host ([string]$bl + ([string]$h * $inner) + [string]$br) -ForegroundColor DarkBlue
    Write-Host ''
    Write-Detail 'Platform : Windows PowerShell'
}

function Resolve-Python {
    <#
    Find a usable Python 3.10+.

    Windows ships a fake `python.exe` stub that opens the Microsoft Store
    instead of running Python. It lives under WindowsApps and reports no
    version, so candidates are validated by actually running them.
    #>
    $candidates = @()
    if ($env:ZELINE_PYTHON) { $candidates += $env:ZELINE_PYTHON }
    $candidates += @('py -3', 'python', 'python3')

    foreach ($candidate in $candidates) {
        $parts = $candidate -split ' ', 2
        $exe   = $parts[0]
        $extra = if ($parts.Count -gt 1) { $parts[1] } else { $null }

        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }

        $probe = 'import sys; print(1 if sys.version_info >= (3, 10) else 0); print(sys.version.split()[0]); print(sys.executable)'
        try {
            $output = if ($extra) { & $exe $extra -c $probe 2>$null } else { & $exe -c $probe 2>$null }
        } catch { continue }

        if ($LASTEXITCODE -ne 0 -or -not $output) { continue }
        $lines = @($output)
        if ($lines.Count -lt 3 -or $lines[0].Trim() -ne '1') { continue }
        # Reject the Microsoft Store alias stub.
        if ($lines[2] -like '*WindowsApps*') { continue }

        return [pscustomobject]@{
            Exe     = $exe
            Extra   = $extra
            Version = $lines[1].Trim()
        }
    }
    return $null
}

function Invoke-Python {
    param(
        [Parameter(Mandatory)] [pscustomobject]$Python,
        [Parameter(Mandatory)] [string[]]$Arguments
    )
    if ($Python.Extra) {
        & $Python.Exe $Python.Extra @Arguments
    } else {
        & $Python.Exe @Arguments
    }
}

function Get-VerifiedReleaseWheel {
    param([Parameter(Mandatory)] [string]$Directory)

    $wheel = Join-Path $Directory $WheelName
    $sums  = Join-Path $Directory 'SHA256SUMS'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseBase/$WheelName" -OutFile $wheel
    Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseBase/SHA256SUMS" -OutFile $sums

    $expected = $null
    foreach ($line in (Get-Content -LiteralPath $sums)) {
        $parts = @($line -split '\s+', 2)
        if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -eq $WheelName) {
            $expected = $parts[0].ToLowerInvariant()
            break
        }
    }
    if (-not $expected) { throw "SHA256SUMS has no entry for $WheelName" }
    if ($expected -notmatch '^[0-9a-f]{64}$') {
        throw "SHA256SUMS expected digest is not 64 hexadecimal characters for $WheelName"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "SHA-256 verification failed for $WheelName" }
    Write-Detail "Verified : $WheelName (SHA-256)"
    return $wheel
}

# ---------------------------------------------------------------- preflight

Show-Banner
if ($PlatformInfo) {
    Write-Step 'PLATFORM'
    Write-Detail 'Target       : Windows PowerShell 5.1+ / PowerShell 7+'
    Write-Detail "Version      : $ReleaseRef (versioned release)"
    Write-Detail 'Prerequisite : Python 3.10+; Git only for checkout installs'
    Write-Detail 'Privilege    : per-user install; Administrator is not required'
    Write-Detail 'Command      : zeline.exe in the Python user Scripts directory'
    exit 0
}
Write-Step 'Installer'

$python = Resolve-Python
if (-not $python) {
    Write-Fail 'No Python 3.10+ found.'
    Write-Detail 'Install it from https://www.python.org/downloads/ and tick'
    Write-Detail '"Add python.exe to PATH" during setup, then re-run this script.'
    Write-Detail 'Note: the `python` that opens the Microsoft Store is a stub, not Python.'
    exit 1
}
$pythonLabel = if ($python.Extra) { "$($python.Exe) $($python.Extra)" } else { $python.Exe }
Write-Detail "Python : $($python.Version) ($pythonLabel)"

# ------------------------------------------------------ local checkout / release

$tempDir    = $null
$installArg = $null

if ($Source) {
    $installArg = (Resolve-Path -LiteralPath $Source).Path
    if (-not (Test-Path -LiteralPath (Join-Path $installArg 'pyproject.toml')) -or
        -not (Test-Path -LiteralPath (Join-Path $installArg 'zeline') -PathType Container)) {
        throw "Not a Zeline checkout: $Source"
    }
    Write-Detail "Source : local checkout ($installArg)"
} else {
    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("zeline-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    Write-Step "Downloading verified release $ReleaseRef..."
    $installArg = Get-VerifiedReleaseWheel -Directory $tempDir
}

try {
    # ------------------------------------------------------------ pip install

    Invoke-Python -Python $python -Arguments @('-m', 'pip', '--version') *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Detail 'pip missing, trying ensurepip...'
        Invoke-Python -Python $python -Arguments @('-m', 'ensurepip', '--upgrade') *> $null
    }
    Invoke-Python -Python $python -Arguments @('-m', 'pip', '--version') *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip is not available for $pythonLabel. Install pip and re-run."
        exit 1
    }

    Write-Step 'Installing/updating package...'
    # --user keeps the install per-account, so no admin prompt is needed.
    $installLog = Invoke-Python -Python $python -Arguments @(
        '-m', 'pip', 'install', '--user', '--upgrade', $installArg
    ) 2>&1
    if ($LASTEXITCODE -ne 0) {
        $installLog | ForEach-Object { Write-Host $_ }
        Write-Fail 'Failed to install the Zeline package. See pip output above.'
        exit 1
    }

    # ------------------------------------------------------------ PATH check

    # pip --user puts console scripts in the per-user Scripts directory, which
    # is frequently missing from PATH on Windows. Detect and offer to fix it.
    #
    # NOTE: it is NOT site.getuserbase() + "\Scripts". On Windows the nt_user
    # scheme is <userbase>\PythonXY\Scripts (version-stamped), so joining
    # "Scripts" directly points at a folder that does not exist and the
    # zeline.exe check always failed. Ask sysconfig for the real path.
    $scriptsProbe = 'import sysconfig,site;print(sysconfig.get_path("scripts","nt_user",vars={"userbase":site.getuserbase()}))'
    $scriptsDir = Invoke-Python -Python $python -Arguments @('-c', $scriptsProbe)
    $scriptsDir = ($scriptsDir | Select-Object -Last 1).Trim()
    $zelineExe  = Join-Path $scriptsDir 'zeline.exe'

    if (Test-Path $zelineExe) {
        Write-Detail "Command: $zelineExe"
    } else {
        Write-Warn "zeline.exe not found in $scriptsDir (the module still works via -m)."
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $onPath   = $false
    if ($userPath) {
        # Wrap in @() before .Count: with Set-StrictMode, a pipeline that yields
        # exactly one object has no .Count property in PowerShell 5.1 and the
        # whole installer aborted with "The property 'Count' cannot be found".
        $matched = @($userPath -split ';' | Where-Object { $_.TrimEnd('\') -ieq $scriptsDir.TrimEnd('\') })
        $onPath  = $matched.Count -gt 0
    }

    if (-not $onPath) {
        Write-Host ''
        Write-Warn 'Scripts folder is not on your PATH:'
        Write-Detail $scriptsDir

        # Decide without prompting when a switch was passed, or when there is no
        # interactive console (downloaded script, CI). Read-Host in a
        # non-interactive session either throws or blocks forever.
        $interactive = -not [Console]::IsInputRedirected
        if ($NoPathUpdate) {
            $shouldAdd = $false
        } elseif ($AddToPath) {
            $shouldAdd = $true
        } elseif (-not $interactive) {
            $shouldAdd = $false
            Write-Detail 'Non-interactive session: leaving PATH unchanged (use -AddToPath).'
        } else {
            $answer = Read-Host '    Add it to your user PATH now? [Y/n]'
            $shouldAdd = ($answer -eq '' -or $answer -match '^[Yy]')
        }

        if ($shouldAdd) {
            $newPath = if ($userPath) { "$userPath;$scriptsDir" } else { $scriptsDir }
            [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
            $env:Path = "$env:Path;$scriptsDir"
            Write-Detail 'Added. Open a new terminal for it to apply everywhere.'
        } else {
            Write-Detail 'Skipped. Run Zeline with:'
            Write-Detail "  $pythonLabel -m zeline.cli"
        }
    }

    # ------------------------------------------------------------ seed skills

    Write-Step 'Initializing Zeline data (~\.zeline)...'
    $seed = 'from zeline import skills; print("    OK - " + str(skills.seed_skills()) + " new skills added")'
    Invoke-Python -Python $python -Arguments @('-c', $seed)
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'Skill seeding failed. Run `zeline doctor` after install to check.'
    }

    # ------------------------------------------------------------ done

    Write-Host ''
    Write-Host 'Zeline installed.' -ForegroundColor Green
    Write-Host ''
    Write-Host 'Start Zeline:'
    Write-Host '  zeline'
    Write-Host ''
    Write-Host 'Pick a gateway with the Up/Down keys, then continue with:'
    Write-Host '  zeline model'
    Write-Host ''
    Write-Host 'Then verify:'
    Write-Host '  zeline doctor'
    Write-Host ''
    Write-Host 'Docs: https://github.com/Mftrferdinand/Zeline'
}
finally {
    if ($tempDir -and (Test-Path $tempDir)) {
        Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
    }
}
