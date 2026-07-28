<#
.SYNOPSIS
    S63 launcher for the S62 forward-only shadow experiment gate.

.DESCRIPTION
    Thin, single-shot wrapper around:

        python -m src.tools.run_scheduled_shadow_cycle --json

    Intended to be invoked from an independent Windows Scheduled
    Task, several minutes after the existing paper trading task.
    This script does not touch the paper task, the paper strategy,
    or broker state in any way — it only launches the S63 gate,
    which itself only reads the paper task's already-written audit
    and cache, and (when the gate passes) advances the S62 shadow
    experiment.

    This script intentionally:
      * takes no hardcoded, developer-specific paths;
      * never passes any order-submission flag — it has no path to
        order submission at all;
      * contains no credentials;
      * makes no network calls;
      * has no retry loop and no infinite loop — it runs the gate
        exactly once and exits.

.PARAMETER RepoRoot
    Absolute path to the trading-bot repository root. Must exist.

.PARAMETER PythonExe
    Absolute path to the Python interpreter to use. Must exist.

.PARAMETER MaxPaperAuditAgeMinutes
    Passed through to --max-paper-audit-age-minutes. Default: 20.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run_s62_shadow_task.ps1 `
        -RepoRoot "C:\path\to\trading-bot" `
        -PythonExe "C:\path\to\venv\Scripts\python.exe"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [int]$MaxPaperAuditAgeMinutes = 20
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RepoRoot -PathType Container)) {
    Write-Error "RepoRoot does not exist or is not a directory: $RepoRoot"
    exit 2
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Error "PythonExe does not exist: $PythonExe"
    exit 2
}

$originalLocation = Get-Location
Set-Location -LiteralPath $RepoRoot
try {
    & $PythonExe -m src.tools.run_scheduled_shadow_cycle `
        --max-paper-audit-age-minutes $MaxPaperAuditAgeMinutes `
        --json
    $exitCode = $LASTEXITCODE
}
finally {
    Set-Location -LiteralPath $originalLocation
}

exit $exitCode
