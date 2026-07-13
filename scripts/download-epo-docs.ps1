$scriptDir = Split-Path -Parent $PSCommandPath
$target = Join-Path $scriptDir "legacy_register_pipeline\download-epo-docs.ps1"
& $target @args
if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }
