param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationNumber,

    [string]$OutputRoot = "markush-run\benchmark",

    [string]$AnalysisJson = "",

    [switch]$GenerateAnalysis,

    [ValidateSet("single", "split")]
    [string]$AnalysisMode = "single",

    [string]$EnvFile = ".env",

    [string]$Model = "",

    [string]$BaseUrl = "",

    [string]$ApiKeyEnv = "OHMYGPT_API_KEY",

    [switch]$RunOcr,

    [switch]$SkipRefine,

    [switch]$ContinueOnDownloadError,

    [int]$TopK = 20,

    [int]$MaxSourceFiles = 3,

    [int]$MaxCharsPerFile = 1800,

    [int]$MaxFieldChars = 1800,

    [int]$MaxPriorArt = 8,

    [int]$MaxTokens = 1000,

    [int]$MetaMaxTokens = 600,

    [int]$RequestTimeout = 180,

    [string]$ReasoningEffort = "low",

    [string]$Verbosity = "low",

    [switch]$WriteAnalysisSteps,

    [int]$EpoRetryCount = 4,

    [int]$EpoRetryDelaySeconds = 3,

    [int]$EpoRequestDelayMilliseconds = 1200,

    [string]$EpoProxyUrl = $env:EPO_PROXY_URL
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if ($ApplicationNumber -notmatch '^EP') {
    $ApplicationNumber = "EP$ApplicationNumber"
}

if ([System.IO.Path]::IsPathRooted($OutputRoot)) {
    $outputRootPath = $OutputRoot
} else {
    $outputRootPath = Join-Path $projectRoot $OutputRoot
}

$caseDir = Join-Path $outputRootPath $ApplicationNumber
$registerDir = Join-Path $caseDir "register"
$docsDir = Join-Path $caseDir "docs"
$originalApplicationDir = Join-Path $caseDir "original-application"
$doclistCsv = Join-Path $registerDir "$ApplicationNumber-doclist.csv"
$benchmarkInput = Join-Path $caseDir "$ApplicationNumber-benchmark-input.json"

New-Item -ItemType Directory -Force -Path $caseDir | Out-Null
New-Item -ItemType Directory -Force -Path $registerDir | Out-Null

Write-Host "[1/8] Fetching EPO register pages..."
$fetchArgs = @{
    ApplicationNumber = $ApplicationNumber
    OutputDir = $registerDir
    RetryCount = $EpoRetryCount
    RetryDelaySeconds = $EpoRetryDelaySeconds
    UseCached = $true
}
if ($EpoProxyUrl) {
    $fetchArgs["ProxyUrl"] = $EpoProxyUrl
}
& (Join-Path $projectRoot "scripts\fetch-epo-main.ps1") @fetchArgs
& (Join-Path $projectRoot "scripts\fetch-epo-doclist.ps1") @fetchArgs

Write-Host "[2/8] Downloading benchmark-relevant EPO documents..."
$titleRegex = "^(?!.*translation)(European search opinion|Supplementary European search report|Communication from the Examining Division|Annex to the communication$|Reply to communication from the Examining Division|Amended claims|Claims|Description|Published international application|Text intended for grant|Communication about intention to grant|Decision to grant|.*refus.*|.*withdrawn.*)"
$downloadArgs = @{
    DocListCsv = $doclistCsv
    OutputDir = $docsDir
    TitleRegex = $titleRegex
    RetryCount = $EpoRetryCount
    RetryDelaySeconds = $EpoRetryDelaySeconds
    RequestDelayMilliseconds = $EpoRequestDelayMilliseconds
}
if ($ContinueOnDownloadError) {
    $downloadArgs["ContinueOnError"] = $true
}
if ($EpoProxyUrl) {
    $downloadArgs["ProxyUrl"] = $EpoProxyUrl
}
& (Join-Path $projectRoot "scripts\download-epo-docs.ps1") @downloadArgs

Write-Host "[3/8] Downloading original application documents..."
$originalTitleRegex = "^(Application documents|Request for grant of a European patent|Description|Claims|Drawings|Abstract|Published international application|Bibliographic data of the European patent application)$"
$originalDownloadArgs = @{
    DocListCsv = $doclistCsv
    OutputDir = $originalApplicationDir
    TitleRegex = $originalTitleRegex
    EarliestPerTitle = $true
    RetryCount = $EpoRetryCount
    RetryDelaySeconds = $EpoRetryDelaySeconds
    RequestDelayMilliseconds = $EpoRequestDelayMilliseconds
}
if ($ContinueOnDownloadError) {
    $originalDownloadArgs["ContinueOnError"] = $true
}
if ($EpoProxyUrl) {
    $originalDownloadArgs["ProxyUrl"] = $EpoProxyUrl
}
& (Join-Path $projectRoot "scripts\download-epo-docs.ps1") @originalDownloadArgs

if ($RunOcr) {
    Write-Host "[4/8] Running OCR on downloaded PDFs..."
    python (Join-Path $projectRoot "scripts\ocr-pdfs.py") $docsDir --zoom 1.6 --overwrite
} else {
    Write-Host "[4/8] OCR skipped. Use -RunOcr when downloaded PDFs are scanned documents."
}

Write-Host "[5/8] Building benchmark input JSON..."
python (Join-Path $projectRoot "scripts\build_benchmark_input.py") $caseDir --application-number $ApplicationNumber --top-k $TopK -o $benchmarkInput

Write-Host "[6/8] Cropping Markush / Formula candidate images..."
python (Join-Path $projectRoot "scripts\render_markush_pages.py") $benchmarkInput --max-pages 6 --candidate-limit 36 --selected-limit 3 --clear

if ($SkipRefine) {
    Write-Host "[7/8] Refine skipped. Analysis stage can run refine later."
} else {
    Write-Host "[7/8] Refining one complete claim and one Markush image via LLM..."
    $refineArgs = @(
        (Join-Path $projectRoot "scripts\refine_benchmark_preview.py"),
        $benchmarkInput,
        "--env-file",
        $EnvFile,
        "--api-key-env",
        $ApiKeyEnv,
        "--temperature",
        "0"
    )
    if ($Model) {
        $refineArgs += @("--model", $Model)
    }
    if ($BaseUrl) {
        $refineArgs += @("--base-url", $BaseUrl)
    }
    python @refineArgs
}

if ($GenerateAnalysis) {
    Write-Host "[8/10] Generating analysis JSON via LLM ($AnalysisMode mode)..."
    if (-not $AnalysisJson) {
        $AnalysisJson = Join-Path $caseDir "$ApplicationNumber-analysis.json"
    }

    $analysisScript = "scripts\generate_analysis_json.py"
    if ($AnalysisMode -eq "split") {
        $analysisScript = "scripts\generate_analysis_json_split.py"
    }

    $llmArgs = @(
        (Join-Path $projectRoot $analysisScript),
        $benchmarkInput,
        "-o",
        $AnalysisJson,
        "--env-file",
        $EnvFile,
        "--api-key-env",
        $ApiKeyEnv,
        "--temperature",
        "0",
        "--max-source-files",
        "$MaxSourceFiles",
        "--max-chars-per-file",
        "$MaxCharsPerFile",
        "--max-tokens",
        "$MaxTokens",
        "--request-timeout",
        "$RequestTimeout"
    )
    if ($AnalysisMode -eq "split") {
        $llmArgs += @(
            "--max-field-chars",
            "$MaxFieldChars",
            "--max-prior-art",
            "$MaxPriorArt",
            "--meta-max-tokens",
            "$MetaMaxTokens",
            "--reasoning-effort",
            $ReasoningEffort,
            "--verbosity",
            $Verbosity
        )
        if ($WriteAnalysisSteps) {
            $llmArgs += "--write-steps"
        }
    }
    if ($Model) {
        $llmArgs += @("--model", $Model)
    }
    if ($BaseUrl) {
        $llmArgs += @("--base-url", $BaseUrl)
    }
    python @llmArgs

    Write-Host "[9/10] Translating risk/action lists..."
    $translateArgs = @(
        (Join-Path $projectRoot "scripts\translate_report_lists.py"),
        $AnalysisJson,
        "--env-file",
        $EnvFile,
        "--api-key-env",
        $ApiKeyEnv,
        "--temperature",
        "0"
    )
    if ($Model) {
        $translateArgs += @("--model", $Model)
    }
    if ($BaseUrl) {
        $translateArgs += @("--base-url", $BaseUrl)
    }
    python @translateArgs
}

if ($AnalysisJson) {
    Write-Host "[10/10] Rendering benchmark output HTML..."
    $htmlOut = [System.IO.Path]::ChangeExtension($AnalysisJson, ".html")
    python (Join-Path $projectRoot "scripts\json_to_html_report.py") $AnalysisJson -o $htmlOut --benchmark-input $benchmarkInput
    Write-Host "Benchmark output HTML: $htmlOut"
} else {
    Write-Host "[8/8] No analysis JSON supplied; benchmark output HTML not rendered."
    Write-Host "After LLM analysis, run:"
    Write-Host "python `"$projectRoot\scripts\json_to_html_report.py`" `"<analysis.json>`""
}

Write-Host "Benchmark input JSON: $benchmarkInput"
