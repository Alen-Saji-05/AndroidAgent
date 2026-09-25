# Run the Android agent on a natural-language task.
#
#   .\agent.ps1 "search for pizza in chrome"
#   .\agent.ps1 "open the play store" --dry-run
#
# Reads GEMINI_API_KEY, GEMINI_MODEL and ADB_PATH from .env, so no flags are
# needed for the common case. Any extra flags (--dry-run, -v, --max-steps N,
# --save-screens DIR) are passed straight through to executor.cli.

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "venv python not found at $py. Create it with: python -m venv .venv"
    exit 1
}
& $py -m executor.cli @args
exit $LASTEXITCODE
