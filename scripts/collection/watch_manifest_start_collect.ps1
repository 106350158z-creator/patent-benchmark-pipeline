param(
    [string]$RepoRoot = "",
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$Snapshot,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$LogsDir,
    [string]$Stamp = "",
    [int]$StartThreshold = 800,
    [int]$Target = 800,
    [int]$CollectWorkers = 2,
    [double]$DeadlineHours = 24,
    [string]$EpoProxyUrl = "http://127.0.0.1:7897",
    [string]$ClashConfig = "",
    [string]$ClashController = "127.0.0.1:9097",
    [string]$ClashHistoryFile = "markush-run\_state\clash-node-rotation-watch.json",
    [switch]$IncludePriorArtDownload
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
Set-Location $RepoRoot

if (-not $Stamp) {
    $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
}

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
$watchLog = Join-Path $LogsDir "watch-manifest-start-collect-$Stamp.log"

function Write-WatchLog([string]$Message) {
    "$(Get-Date -Format o) $Message" | Add-Content -Encoding UTF8 $watchLog
}

function Get-ManifestRecordCount([string]$Path) {
    if (-not (Test-Path $Path)) {
        return 0
    }
    try {
        $json = Get-Content $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $json.records) {
            return 0
        }
        return @($json.records).Count
    }
    catch {
        return 0
    }
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ProcessId }
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$deadline = (Get-Date).AddHours($DeadlineHours)
$collect = $null
Write-WatchLog "watcher started manifest=$Manifest threshold=$StartThreshold target=$Target"

while ((Get-Date) -lt $deadline) {
    $accepted = Get-ManifestRecordCount $Manifest
    Write-WatchLog "accepted=$accepted"

    if ($null -eq $collect -and $accepted -ge $StartThreshold) {
        Copy-Item -LiteralPath $Manifest -Destination $Snapshot -Force
        Write-WatchLog "threshold reached; copied snapshot=$Snapshot"

        $collectOut = Join-Path $LogsDir "collect-$Stamp.log"
        $collectErr = Join-Path $LogsDir "collect-$Stamp.err.log"
        $collectArgs = @(
            "scripts\run_target_benchmark_raw_materials.py",
            "--candidate-source", "manifest",
            "--manifest", $Snapshot,
            "--output-root", $OutputRoot,
            "--target", "$Target",
            "--collect-workers", "$CollectWorkers",
            "--skip-candidate-collection",
            "--skip-manifest-validation",
            "--epo-proxy-url", $EpoProxyUrl,
            "--clash-auto-rotate",
            "--clash-config", $ClashConfig,
            "--clash-controller", $ClashController,
            "--clash-history-file", $ClashHistoryFile,
            "--clash-retries-per-record", "1",
            "--clash-rotate-sleep-seconds", "20",
            "--clash-bad-cooldown-seconds", "1800"
        )
        if ($IncludePriorArtDownload) {
            $collectArgs += "--include-prior-art-download"
        }

        Write-WatchLog ("starting collect: python " + ($collectArgs -join " "))
        $collect = Start-Process -FilePath python -ArgumentList $collectArgs -WorkingDirectory $RepoRoot -RedirectStandardOutput $collectOut -RedirectStandardError $collectErr -WindowStyle Hidden -PassThru
        Write-WatchLog "collect pid=$($collect.Id)"
    }

    if ($null -ne $collect -and $collect.HasExited) {
        Write-WatchLog "collect exited code=$($collect.ExitCode)"
        break
    }

    Start-Sleep -Seconds 30
}

if ((Get-Date) -ge $deadline) {
    Write-WatchLog "deadline reached"
    if ($null -ne $collect -and -not $collect.HasExited) {
        Stop-ProcessTree $collect.Id
        Write-WatchLog "stopped collect process tree"
    }
}

Write-WatchLog "watcher finished"
