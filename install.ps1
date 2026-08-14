# Zeline installer for Windows PowerShell.
#
# Usage (PowerShell, no admin needed):
#   irm https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.ps1 | iex
#
# Or from a local clone:
#   git clone https://github.com/Mftrferdinand/Zerolinear.git
#   cd Zerolinear
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Optional environment variables:
#   $env:ZELINE_PYTHON       = "python"       # Python executable to use
#   $env:ZELINE_INSTALL_DIR  = "C:\zeline"    # unused for pip installs, kept for parity

#Requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoUrl = 'https://github.com/Mftrferdinand/Zerolinear.git'
$Branch  = 'main'

# ---------------------------------------------------------------- utilities

function Write-Step   { param([string]$Text) Write-Host "==> $Text" }
function Write-Detail { param([string]$Text) Write-Host "    $Text" }
function Write-Fail   { param([string]$Text) Write-Host "[x] $Text" -ForegroundColor Red }
function Write-Warn   { param([string]$Text) Write-Host "[!] $Text" -ForegroundColor Yellow }

function Show-Banner {
    $title    = 'Z  E  L  I  N  E'
    $subtitle = 'AGENTIC AI BY ZEROLINEAR - v0.1.0'
    $inner    = $subtitle.Length + 6
    $bar      = '-' * $inner

    function Format-Centered {
        param([string]$Text, [int]$Width)
        $pad  = [math]::Floor(($Width - $Text.Length) / 2)
        $left = ' ' * $pad
        $right = ' ' * ($Width - $Text.Length - $pad)
        return "$left$Text$right"
    }

    $t = Format-Centered -Text $title    -Width $inner
    $s = Format-Centered -Text $subtitle -Width $inner
    Write-Host ''
    Write-Host "+$bar+"           -ForegroundColor DarkCyan
    Write-Host "|$t|"             -ForegroundColor White
    Write-Host "+$bar+"           -ForegroundColor DarkCyan
    Write-Host "|$s|"             -ForegroundColor Cyan
    Write-Host "+$bar+"           -ForegroundColor DarkCyan
    Write-Host ''
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

# ---------------------------------------------------------------- preflight

Show-Banner
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

# ---------------------------------------------------------------- source

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$tempDir   = $null
$sourceDir = $null

if ((Test-Path (Join-Path $scriptDir 'pyproject.toml')) -and
    (Test-Path (Join-Path $scriptDir 'zeline'))) {
    $sourceDir = $scriptDir
    Write-Detail "Source : local checkout ($sourceDir)"
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Fail 'git not found. Install Git for Windows: https://git-scm.com/download/win'
        exit 1
    }
    $tempDir   = Join-Path ([System.IO.Path]::GetTempPath()) ("zeline-" + [guid]::NewGuid().ToString('N'))
    $sourceDir = Join-Path $tempDir 'zeline'
    Write-Step 'Downloading Zeline source...'
    git clone --depth 1 --branch $Branch $RepoUrl $sourceDir
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'git clone failed.'
        exit 1
    }
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
        '-m', 'pip', 'install', '--user', '--upgrade', $sourceDir
    ) 2>&1
    if ($LASTEXITCODE -ne 0) {
        $installLog | ForEach-Object { Write-Host $_ }
        Write-Fail 'Failed to install the Zeline package. See pip output above.'
        exit 1
    }

    # ------------------------------------------------------------ PATH check

    # pip --user puts console scripts in the per-user Scripts directory, which
    # is frequently missing from PATH on Windows. Detect and offer to fix it.
    $scriptsDir = Invoke-Python -Python $python -Arguments @(
        '-c', 'import site,os;base=site.getuserbase();print(os.path.join(base, "Scripts"))'
    )
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
        $onPath = ($userPath -split ';' | Where-Object { $_.TrimEnd('\') -ieq $scriptsDir.TrimEnd('\') }).Count -gt 0
    }

    if (-not $onPath) {
        Write-Host ''
        Write-Warn 'Scripts folder is not on your PATH:'
        Write-Detail $scriptsDir
        $answer = Read-Host '    Add it to your user PATH now? [Y/n]'
        if ($answer -eq '' -or $answer -match '^[Yy]') {
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
    Write-Host 'Docs: https://github.com/Mftrferdinand/Zerolinear'
}
finally {
    if ($tempDir -and (Test-Path $tempDir)) {
        Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
    }
}
