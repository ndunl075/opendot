<#
.SYNOPSIS
    Set up Alfred Core locally: create the venv, install the package, and
    initialize the database.

.DESCRIPTION
    Turns README.md's "Local setup" section (venv, pip install, alfred init)
    into one repeatable script, and optionally automates "Running
    continuously"'s documented Task Scheduler workaround for decision 3's
    "always-on PC" requirement. Safe to re-run: it skips venv creation when
    one already exists rather than recreating it, and every mutating step is
    wrapped so `-WhatIf` previews the exact actions without touching disk,
    the database, or the Task Scheduler.

    This script only ever reaches your own machine. It never contacts
    Google, Telegram, Slack, GitHub, or any other provider -- connector
    credentials are a separate, explicit step you run afterward (see the
    printed next steps, or README.md's "Local connectors" section).

.PARAMETER VenvPath
    Where to create the virtual environment. Defaults to .venv next to the
    repository root.

.PARAMETER RegisterScheduledTask
    Also register a Windows Task Scheduler entry that runs `alfred run` at
    user logon. Off by default -- installing Alfred should never silently
    start a background process.

.PARAMETER TaskName
    Name for the Task Scheduler entry. Only used with -RegisterScheduledTask.

.PARAMETER RunArgs
    Extra arguments appended to the scheduled `alfred run` command, for
    example "--pair 123:456 --chat-id 123". Only used with
    -RegisterScheduledTask.

.EXAMPLE
    .\scripts\install.ps1

.EXAMPLE
    .\scripts\install.ps1 -RegisterScheduledTask -RunArgs "--pair 123:456 --chat-id 123"

.EXAMPLE
    .\scripts\install.ps1 -WhatIf
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$VenvPath,
    [switch]$RegisterScheduledTask,
    [string]$TaskName = "Alfred",
    [string]$RunArgs = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $VenvPath) {
    $VenvPath = Join-Path $repoRoot ".venv"
}

function Assert-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw "Python was not found on PATH. Install Python 3.12+ first: https://www.python.org/downloads/"
    }
    $versionOutput = (& python --version) 2>&1
    if ($versionOutput -notmatch "Python 3\.(1[2-9]|[2-9]\d)") {
        Write-Warning "Detected '$versionOutput'; Alfred requires Python 3.12+ (see pyproject.toml)."
    }
}

Write-Host "==> Checking Python" -ForegroundColor Cyan
Assert-Python

$venvPython = Join-Path $VenvPath "Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "==> Virtual environment already exists at $VenvPath" -ForegroundColor Cyan
} elseif ($PSCmdlet.ShouldProcess($VenvPath, "Create virtual environment")) {
    Write-Host "==> Creating virtual environment at $VenvPath" -ForegroundColor Cyan
    & python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed with exit code $LASTEXITCODE" }
}

if ($PSCmdlet.ShouldProcess($repoRoot, "pip install -e .[dev]")) {
    Write-Host "==> Installing alfred-core (editable, with dev extras)" -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip -q
    & $venvPython -m pip install -e "$repoRoot[dev]" -q
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
}

$alfred = Join-Path $VenvPath "Scripts\alfred.exe"

if ($PSCmdlet.ShouldProcess("the local database", "alfred init")) {
    Write-Host "==> Initializing the local database" -ForegroundColor Cyan
    & $alfred init
    & $alfred status
}

if ($RegisterScheduledTask -and $PSCmdlet.ShouldProcess($TaskName, "Register Task Scheduler entry (runs 'alfred run' at logon)")) {
    Write-Host "==> Registering Task Scheduler entry '$TaskName'" -ForegroundColor Cyan
    $pythonw = Join-Path $VenvPath "Scripts\pythonw.exe"
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m alfred.cli run $RunArgs" -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force -Confirm:$false | Out-Null
    Write-Host "    Registered. Inspect it with: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
    Write-Host "    Remove it with: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "==> Alfred is installed." -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Store any connector credentials you plan to use (README.md 'Local connectors')."
Write-Host "  2. Run '$alfred google-auth' for Calendar/Gmail; 'connector-status' shows what's configured."
Write-Host "  3. Run '$alfred backup-key-generate' before your first 'alfred backup-create'."
if (-not $RegisterScheduledTask) {
    Write-Host "  4. Start Alfred with '$alfred run <flags>', or re-run this script with -RegisterScheduledTask to keep it running at logon."
}
