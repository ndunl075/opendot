param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$alfredExe = Join-Path $RepositoryRoot ".venv\Scripts\alfred.exe"
$database = Join-Path $RepositoryRoot ".alfred\alfred.db"
$backupDirectory = Join-Path $RepositoryRoot ".alfred\backups"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$output = Join-Path $backupDirectory "alfred-$timestamp.alfred-backup"

New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
& $alfredExe --db $database backup-create --output $output
if ($LASTEXITCODE -ne 0) {
    throw "Alfred backup failed with exit code $LASTEXITCODE"
}
