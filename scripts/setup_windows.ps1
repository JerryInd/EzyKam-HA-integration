#Requires -Version 5.1
<#
.SYNOPSIS
    Windows development setup for the CP Plus Ezykam integration.

.DESCRIPTION
    Creates a virtual environment, installs runtime + test dependencies and
    runs the test suite. Nothing is installed system-wide.

    Home Assistant itself targets Linux; this script is for developing and
    running the protocol library and its tests on a Windows machine.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== CP Plus Ezykam development setup ==" -ForegroundColor Cyan
Write-Host "Root: $Root"

# --- Python -------------------------------------------------------------
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    Write-Error "Python 3.11+ was not found on PATH. Install Python 3.12 from python.org first."
    exit 1
}
$pyVersion = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
Write-Host "Python: $pyVersion"
if ([version]$pyVersion -lt [version]"3.11") {
    Write-Error "Python 3.11 or newer is required (code targets 3.12)."
    exit 1
}

# --- Virtual environment ------------------------------------------------
$Venv = Join-Path $Root ".venv"
if ($Recreate -and (Test-Path $Venv)) {
    Write-Host "Removing existing .venv ..."
    Remove-Item $Venv -Recurse -Force
}
if (-not (Test-Path $Venv)) {
    Write-Host "Creating virtual environment ..."
    python -m venv $Venv
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"

& $VenvPython -m pip install --upgrade pip --quiet

# --- Dependencies -------------------------------------------------------
Write-Host "Installing dependencies ..."
& $VenvPython -m pip install --quiet -r requirements-dev.txt

# --- Session ------------------------------------------------------------
$SessionDir = Join-Path $Root ".session"
if (-not (Test-Path $SessionDir)) {
    New-Item -ItemType Directory -Path $SessionDir | Out-Null
}
$SessionFile = Join-Path $SessionDir "ezykam_session.json"
if (Test-Path $SessionFile) {
    Write-Host "Existing session found: $SessionFile" -ForegroundColor Green
} else {
    Write-Host "No session yet. Create one with:" -ForegroundColor Yellow
    Write-Host "    & `$VenvPython scripts\qr_login.py --verify"
    Write-Host "  (a QR code will open for you to scan with the ezyKam+ app)"
}

# --- Git hygiene --------------------------------------------------------
# git is optional: this folder may not be a repository yet, and PowerShell 5.1
# promotes native stderr to a terminating error under Stop, so the preference
# is relaxed for these probes and restored immediately afterwards.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
git rev-parse --is-inside-work-tree *> $null
$isRepo = ($LASTEXITCODE -eq 0)

if ($isRepo) {
    Write-Host "Verifying secrets are git-ignored ..."
    foreach ($pattern in @(".session/", "*.har", "captures/", ".env")) {
        $hit = git check-ignore $pattern 2>$null
        if ($hit) {
            Write-Host "  ignored: $pattern" -ForegroundColor Green
        } else {
            Write-Warning "  NOT ignored: $pattern - check .gitignore"
        }
    }
} else {
    Write-Host "Not a git repository yet - skipping ignore checks."
}
$ErrorActionPreference = $prevEAP

# --- Tests --------------------------------------------------------------
if (-not $SkipTests) {
    Write-Host "Running test suite ..." -ForegroundColor Cyan
    & $VenvPython -m pytest
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Tests failed."
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Activate : .venv\Scripts\activate"
Write-Host "  Tests    : python -m pytest"
Write-Host "  Login    : python scripts\qr_login.py --verify"
Write-Host "  Discover : python scripts\discover_live.py"
Write-Host "  Stream   : python scripts\stream_test.py"
