<#
.SYNOPSIS
    Register Alfred's phone-friendly watchdog and restart tasks.

.DESCRIPTION
    Creates two Task Scheduler entries:

    - AlfredWatchdog: runs `alfred watchdog-check` every five minutes. If the
      runner heartbeat is stale, it restarts the Windows service (or falls
      back to the configured `alfred run` command) and polls Telegram once
      for `/wake` or `/restart`.
    - AlfredRestart: on-demand restart used by Telegram `/restart` and the
      watchdog. Runs with highest privileges so a phone command does not need
      an Administrator prompt each time.

    Safe to re-run; existing tasks are replaced.

.EXAMPLE
    .\scripts\register-watchdog.ps1
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$VenvPath,
    [string]$WatchdogTaskName = "AlfredWatchdog",
    [string]$RestartTaskName = "AlfredRestart",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $VenvPath) {
    $VenvPath = Join-Path $repoRoot ".venv"
}
$alfred = Join-Path $VenvPath "Scripts\alfred.exe"
$alfredService = Join-Path $VenvPath "Scripts\alfred-service.exe"
if (-not (Test-Path $alfred)) {
    throw "Alfred CLI not found at $alfred. Run .\scripts\install.ps1 first."
}

$watchdogAction = New-ScheduledTaskAction `
    -Execute $alfred `
    -Argument "watchdog-check" `
    -WorkingDirectory $repoRoot
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)
$watchdogSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess($WatchdogTaskName, "Register watchdog scheduled task")) {
    Register-ScheduledTask `
        -TaskName $WatchdogTaskName `
        -Action $watchdogAction `
        -Trigger $watchdogTrigger `
        -Settings $watchdogSettings `
        -Force `
        -Confirm:$false | Out-Null
    Write-Host "Registered $WatchdogTaskName (every $IntervalMinutes minutes)." -ForegroundColor Green
}

if (-not (Test-Path $alfredService)) {
    Write-Warning "alfred-service.exe not found; skipping $RestartTaskName. Install pywin32 and use the Windows service for best results."
    exit 0
}

$restartAction = New-ScheduledTaskAction `
    -Execute $alfredService `
    -Argument "restart" `
    -WorkingDirectory $repoRoot
$restartSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

if ($PSCmdlet.ShouldProcess($RestartTaskName, "Register on-demand restart task")) {
    Register-ScheduledTask `
        -TaskName $RestartTaskName `
        -Action $restartAction `
        -Settings $restartSettings `
        -Force `
        -Confirm:$false | Out-Null
    schtasks.exe /Change /TN $RestartTaskName /RU "$env:USERDOMAIN\$env:USERNAME" /RL HIGHEST | Out-Null
    Write-Host "Registered $RestartTaskName (on-demand, highest privileges)." -ForegroundColor Green
}

Write-Host ""
Write-Host "From Telegram on your phone:" -ForegroundColor Cyan
Write-Host "  /status   - is Alfred running?"
Write-Host "  /restart  - restart now"
Write-Host "  /wake     - same as /restart when Alfred is down"
Write-Host ""
Write-Host "The watchdog also auto-restarts a stale runner every $IntervalMinutes minutes."
