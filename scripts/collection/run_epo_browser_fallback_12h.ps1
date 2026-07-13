param(
    [string]$RepoRoot = "",
    [string]$Candidates = "markush-run\benchmark\ep_application_candidates_full_20260709b.json",
    [string]$Manifest = "markush-run\benchmark\ep_review_file_sources_full_20260709b.json",
    [string]$Snapshot = "markush-run\benchmark\ep_review_file_sources_full_20260709b.start800.json",
    [string]$OutputRoot = "markush-run\benchmark-full-20260709b",
    [string]$CacheRoot = "markush-run\benchmark\full-20260709b-doclist-cache",
    [int]$ManifestTarget = 1000,
    [int]$StartCollectAt = 800,
    [int]$CollectTarget = 800,
    [int]$ValidationWorkers = 1,
    [int]$CollectWorkers = 2,
    [double]$DeadlineHours = 12,
    [string]$EpoProxyUrl = "http://127.0.0.1:7897",
    [string]$BrowserProfileDir = "markush-run\_state\epo-register-browser-profile",
    [string]$ClashConfig = "C:\Users\de'l'l\AppData\Roaming\io.github.clash-verge-rev.clash-verge-rev\config.yaml",
    [string]$ClashController = "127.0.0.1:9097",
    [string]$ClashHistoryFile = "markush-run\_state\clash-node-rotation-full-20260709b.json",
    [switch]$IncludePriorArtDownload
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

$orchestratorLog = Join-Path $logsDir "browser-fallback-12h-$stamp.orchestrator.log"
$pidFile = Join-Path $logsDir "browser-fallback-12h-$stamp.pid.txt"

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

function Get-RecordCount([string]$JsonPath) {
    if (-not (Test-Path -LiteralPath $JsonPath)) {
        return 0
    }
    try {
        $json = Get-Content -LiteralPath $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -eq $json.records) {
            return 0
        }
        return @($json.records).Count
    }
    catch {
        return 0
    }
}

Write-OrchestratorLog "started deadlineHours=$DeadlineHours candidates=$Candidates manifest=$Manifest output=$OutputRoot"

$validationOut = Join-Path $logsDir "validate-browser-fallback-$stamp.log"
$validationErr = Join-Path $logsDir "validate-browser-fallback-$stamp.err.log"
$watcherOut = Join-Path $logsDir "watcher-browser-fallback-$stamp.out.log"
$watcherErr = Join-Path $logsDir "watcher-browser-fallback-$stamp.err.log"

$validationArgs = @(
    "scripts\build_target_review_manifest.py",
    "--candidates", $Candidates,
    "--output", $Manifest,
    "--target", "$ManifestTarget",
    "--cache-root", $CacheRoot,
    "--workers", "$ValidationWorkers",
    "--retry-count", "1",
    "--retry-delay-seconds", "2",
    "--epo-proxy-url", $EpoProxyUrl,
    "--browser-doclist-fallback",
    "--browser-profile-dir", $BrowserProfileDir,
    "--browser-proxy-server", $EpoProxyUrl,
    "--browser-manual-wait-seconds", "90"
)

$watcherArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "scripts\collection\watch_manifest_start_collect.ps1",
    "-Manifest", $Manifest,
    "-Snapshot", $Snapshot,
    "-OutputRoot", $OutputRoot,
    "-LogsDir", $logsDir,
    "-Stamp", $stamp,
    "-StartThreshold", "$StartCollectAt",
    "-Target", "$CollectTarget",
    "-CollectWorkers", "$CollectWorkers",
    "-DeadlineHours", "$DeadlineHours",
    "-EpoProxyUrl", $EpoProxyUrl,
    "-ClashConfig", $ClashConfig,
    "-ClashController", $ClashController,
    "-ClashHistoryFile", $ClashHistoryFile
)
if ($IncludePriorArtDownload) {
    $watcherArgs += "-IncludePriorArtDownload"
}

Write-OrchestratorLog ("validation command: python " + ($validationArgs -join " "))
$validation = Start-Process -FilePath python -ArgumentList $validationArgs -WorkingDirectory $RepoRoot -RedirectStandardOutput $validationOut -RedirectStandardError $validationErr -WindowStyle Hidden -PassThru
Write-OrchestratorLog "validation pid=$($validation.Id)"

Write-OrchestratorLog ("watcher command: powershell " + ($watcherArgs -join " "))
$watcher = Start-Process -FilePath powershell.exe -ArgumentList $watcherArgs -WorkingDirectory $RepoRoot -RedirectStandardOutput $watcherOut -RedirectStandardError $watcherErr -WindowStyle Hidden -PassThru
Write-OrchestratorLog "watcher pid=$($watcher.Id)"

@(
    "stamp=$stamp",
    "orchestrator_pid=$PID",
    "validation_pid=$($validation.Id)",
    "watcher_pid=$($watcher.Id)",
    "orchestrator_log=$orchestratorLog",
    "validation_out=$validationOut",
    "validation_err=$validationErr",
    "watcher_out=$watcherOut",
    "watcher_err=$watcherErr",
    "manifest=$Manifest",
    "snapshot=$Snapshot",
    "output_root=$OutputRoot"
) | Set-Content -Encoding UTF8 $pidFile

$deadline = (Get-Date).AddHours($DeadlineHours)
while ((Get-Date) -lt $deadline) {
    $accepted = Get-RecordCount $Manifest
    Write-OrchestratorLog "heartbeat accepted=$accepted validationExited=$($validation.HasExited) watcherExited=$($watcher.HasExited)"
    if ($validation.HasExited -and $watcher.HasExited) {
        break
    }
    Start-Sleep -Seconds 300
}

if ((Get-Date) -ge $deadline) {
    Write-OrchestratorLog "deadline reached; stopping active process trees"
    if (-not $validation.HasExited) {
        Stop-ProcessTree $validation.Id
        Write-OrchestratorLog "stopped validation tree"
    }
    if (-not $watcher.HasExited) {
        Stop-ProcessTree $watcher.Id
        Write-OrchestratorLog "stopped watcher tree"
    }
}

$finalAccepted = Get-RecordCount $Manifest
Write-OrchestratorLog "finished finalAccepted=$finalAccepted validationExit=$($validation.ExitCode) watcherExit=$($watcher.ExitCode)"
