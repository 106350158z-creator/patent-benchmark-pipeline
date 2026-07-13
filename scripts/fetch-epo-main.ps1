$scriptDir = Split-Path -Parent $PSCommandPath
$target = Join-Path $scriptDir "legacy_register_pipeline\fetch-epo-main.ps1"

$ErrorActionPreference = "Stop"
try {
    & $target @args
    if (($null -ne $LASTEXITCODE) -and ($LASTEXITCODE -ne 0)) {
        exit $LASTEXITCODE
    }
    if (-not $?) {
        exit 1
    }
} catch {
    Write-Error $_
    exit 1
}
