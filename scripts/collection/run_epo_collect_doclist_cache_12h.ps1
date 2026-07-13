param(
    [string]$RepoRoot = "",
    [string]$Manifest = "markush-run\benchmark\ep_review_file_sources_full_20260709b.start800.json",
    [string]$OutputRoot = "markush-run\benchmark-full-20260709b",
    [string]$DoclistCacheRoot = "markush-run\benchmark\full-20260709b-doclist-cache",
    [int]$Target = 800,
    [int]$CollectWorkers = 1,
    [double]$DeadlineHours = 12,
    [string]$EpoProxyUrl = "http://127.0.0.1:7897",
    [string]$BrowserProfileDir = "markush-run\_state\epo-register-browser-profile",
    [string]$BrowserChannel = "chrome",
    [int]$BrowserManualWaitSeconds = 5,
    [switch]$UseBrowserFallback,
    [string]$ClashConfig = "C:\Users\de'l'l\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev\config.yaml",
    [string]$ClashController = "127.0.0.1:9097",
    [string]$ClashSelector = "node-selection",
    [string]$ClashHistoryFile = "markush-run\_state\clash-node-rotation-full-20260709b-collect.json"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
Set-Location $RepoRoot

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logsDir = Join-Path $OutputRoot "_logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$orchestratorLog = Join-Path $logsDir "collect-doclist-cache-12h-$stamp.orchestrator.log"
$collectOut = Join-Path $logsDir "collect-doclist-cache-12h-$stamp.out.log"
$collectErr = Join-Path $logsDir "collect-doclist-cache-12h-$stamp.err.log"
$pidFile = Join-Path $logsDir "collect-doclist-cache-12h-$stamp.pid.txt"

function Write-OrchestratorLog([string]$Message) {
    "$(Get-Date -Format o) $Message" | Add-Content -Encoding UTF8 $orchestratorLog
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ProcessId }
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$collectArgs = @(
    "scripts\run_manifest_benchmark_batch.py",
    "--manifest", $Manifest,
    "--output-root", $OutputRoot,
    "--stage", "collect",
    "--skip-existing",
    "--success-target", "$Target",
    "--workers", "$CollectWorkers",
    "--fetch-only",
    "--epo-proxy-url", $EpoProxyUrl,
    "--epo-retry-count", "1",
    "--epo-retry-delay-seconds", "2",
    "--epo-request-delay-milliseconds", "800",
    "--epo-request-timeout-seconds", "30",
    "--doclist-cache-root", $DoclistCacheRoot,
    "--skip-register-main-fetch",
    "--fetch-success-policy", "doclist-complete",
    "--clash-auto-rotate",
    "--clash-config", $ClashConfig,
    "--clash-controller", $ClashController,
    "--clash-selector", $ClashSelector,
    "--clash-history-file", $ClashHistoryFile,
    "--clash-retries-per-record", "1",
    "--clash-rotate-sleep-seconds", "15",
    "--clash-bad-cooldown-seconds", "1800"
)

if ($UseBrowserFallback) {
    $collectArgs += @(
        "--browser-register-fallback",
        "--browser-profile-dir", $BrowserProfileDir,
        "--browser-proxy-server", $EpoProxyUrl,
        "--browser-manual-wait-seconds", "$BrowserManualWaitSeconds",
        "--browser-start-minimized"
    )
}

Write-OrchestratorLog "starting collect-only manifest=$Manifest output=$OutputRoot deadlineHours=$DeadlineHours"
Write-OrchestratorLog ("command: python " + ($collectArgs -join " "))
$collect = Start-Process -FilePath python -ArgumentList $collectArgs -WorkingDirectory $RepoRoot -RedirectStandardOutput $collectOut -RedirectStandardError $collectErr -WindowStyle Hidden -PassThru

@(
    "stamp=$stamp",
    "orchestrator_pid=$PID",
    "collect_pid=$($collect.Id)",
    "orchestrator_log=$orchestratorLog",
    "collect_out=$collectOut",
    "collect_err=$collectErr",
    "manifest=$Manifest",
    "output_root=$OutputRoot"
) | Set-Content -Encoding UTF8 $pidFile

$deadline = (Get-Date).AddHours($DeadlineHours)
while ((Get-Date) -lt $deadline) {
    Write-OrchestratorLog "heartbeat collectExited=$($collect.HasExited)"
    if ($collect.HasExited) {
        break
    }
    Start-Sleep -Seconds 300
}

if ((Get-Date) -ge $deadline -and -not $collect.HasExited) {
    Write-OrchestratorLog "deadline reached; stopping collect process tree"
    Stop-ProcessTree $collect.Id
}

Write-OrchestratorLog "finished collectExit=$($collect.ExitCode)"
