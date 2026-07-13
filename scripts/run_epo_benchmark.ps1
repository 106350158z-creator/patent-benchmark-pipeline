$scriptDir = Split-Path -Parent $PSCommandPath
$target = Join-Path $scriptDir "legacy_register_pipeline\run_epo_benchmark.ps1"
$overviewScript = Join-Path $scriptDir "collection\build_benchmark_overview.py"

$ErrorActionPreference = "Stop"
$exitCode = 0
try {
    & $target @args
    if (($null -ne $LASTEXITCODE) -and ($LASTEXITCODE -ne 0)) {
        $exitCode = $LASTEXITCODE
    }
    elseif (-not $?) {
        $exitCode = 1
    }
} catch {
    Write-Error $_
    $exitCode = 1
}

# Refresh after failures too: partial downloads are useful inventory data.
try {
    & python $overviewScript
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Benchmark overview refresh failed with exit code $LASTEXITCODE."
    }
} catch {
    Write-Warning "Benchmark overview refresh failed: $_"
}

exit $exitCode
