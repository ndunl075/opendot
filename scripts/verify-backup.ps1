<#
.SYNOPSIS
    Rehearse restoring the newest encrypted backup, without touching the live database.

.DESCRIPTION
    ARCHITECTURE.md section 8 requires testing restore monthly. `alfred
    backup-verify` performs that rehearsal against a throwaway copy; this
    script is the scheduled wrapper around it, and the companion to
    backup.ps1, which creates the snapshots this one checks.

    A backup nobody has ever restored is a guess, not a backup. The failure
    this exists to catch is the quiet one: snapshots accumulating nightly for
    months while the key has rotated, the disk has silently corrupted a file,
    or the schema has moved on -- all invisible until the day you actually
    need one.

    Exits non-zero when verification fails, so Task Scheduler records a
    failed run rather than a successful one that happened to log bad news.

.PARAMETER RepositoryRoot
    The alfred checkout. Defaults to this script's parent directory.

.PARAMETER BackupDirectory
    Where snapshots live. Defaults to <root>\.alfred\backups, matching
    backup.ps1.

.PARAMETER LogPath
    Appends one JSON line per run. Defaults to
    <root>\.alfred\backup-verify.log, so a scheduled run leaves evidence
    rather than only an exit code.
#>
param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$BackupDirectory,
    [string]$LogPath
)

$ErrorActionPreference = "Stop"

$alfredExe = Join-Path $RepositoryRoot ".venv\Scripts\alfred.exe"
$database = Join-Path $RepositoryRoot ".alfred\alfred.db"
if (-not $BackupDirectory) {
    $BackupDirectory = Join-Path $RepositoryRoot ".alfred\backups"
}
if (-not $LogPath) {
    $LogPath = Join-Path $RepositoryRoot ".alfred\backup-verify.log"
}

if (-not (Test-Path $BackupDirectory)) {
    throw "Backup directory does not exist: $BackupDirectory"
}

# stdout carries the JSON report even on failure; the exit code is the verdict.
$report = & $alfredExe --db $database backup-verify --latest-in $BackupDirectory
$verifyExitCode = $LASTEXITCODE

$line = [ordered]@{
    ran_at    = (Get-Date).ToUniversalTime().ToString("o")
    exit_code = $verifyExitCode
    report    = $report
} | ConvertTo-Json -Compress -Depth 4
Add-Content -Path $LogPath -Value $line -Encoding utf8

Write-Output $report
if ($verifyExitCode -ne 0) {
    # Surfaced as a failed scheduled task, not a cheerful run that logged
    # "ok": false into a file nobody opens.
    throw "Alfred backup verification FAILED (exit code $verifyExitCode). The newest backup in $BackupDirectory did not restore cleanly."
}
Write-Output "Backup verification passed."
