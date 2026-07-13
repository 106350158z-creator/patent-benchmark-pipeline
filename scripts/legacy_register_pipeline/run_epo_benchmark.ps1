param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationNumber,

    [string]$OutputRoot = "markush-run\benchmark",

    [string]$AnalysisJson = "",

    [switch]$GenerateAnalysis,

    [ValidateSet("single", "split")]
    [string]$AnalysisMode = "split",

    [string]$EnvFile = ".env",

    [string]$Model = "",

    [string]$BaseUrl = "",

    [string]$ApiKeyEnv = "OHMYGPT_API_KEY",

    [switch]$RunOcr,

    [switch]$ExtractPdfText,

    [ValidateSet("docs", "original", "both")]
    [string]$OcrScope = "docs",

    [string]$OcrIncludeRegex = "claims|communication|decision|annex|reply|search_opinion|search_report|amended_claims",

    [string]$OcrExcludeRegex = "translation|description|published_international|text_intended",

    [int]$OcrWorkers = 1,

    [int]$OcrTimeoutSeconds = 1200,

    [double]$OcrZoom = 1.6,

    [switch]$SkipRefine,

    [switch]$ContinueOnDownloadError,

    [int]$TopK = 20,

    [int]$MaxSourceFiles = 8,

    [int]$MaxCharsPerFile = 5000,

    [int]$MaxFieldChars = 5000,

    [int]$MaxPriorArt = 12,

    [int]$MaxTokens = 3000,

    [int]$MetaMaxTokens = 1200,

    [int]$RequestTimeout = 240,

    [string]$ReasoningEffort = "low",

    [string]$Verbosity = "low",

    [int]$Retries = 3,

    [switch]$WriteAnalysisSteps,

    [switch]$SkipCompleteSplitAnalysis,

    [int]$CompleteMaxSourceFiles = 8,

    [int]$CompleteMaxCharsPerFile = 5000,

    [int]$CompleteMaxFieldChars = 5000,

    [int]$CompleteMaxPriorArt = 12,

    [int]$CompleteMaxTokens = 3000,

    [int]$CompleteMetaMaxTokens = 1200,

    [int]$CompleteRequestTimeout = 240,

    [int]$CompleteRetries = 1,

    [int]$CompletePasses = 2,

    [string]$CompleteReasoningEffort = "low",

    [string]$CompleteVerbosity = "low",

    [switch]$SkipRepairEvidence,

    [switch]$ContinueOnVerifyError,

    [double]$RepairMinScore = 0.55,

    [double]$VerifyMinScore = 0.88,

    [int]$EpoRetryCount = 4,

    [int]$EpoRetryDelaySeconds = 3,

    [int]$EpoRequestDelayMilliseconds = 1200,

    [int]$EpoRequestTimeoutSeconds = 60,

    [string]$EpoProxyUrl = $env:EPO_PROXY_URL,

    [switch]$BrowserRegisterFallback,

    [string]$BrowserProfileDir = "markush-run\_state\epo-register-browser-profile",

    [string]$BrowserProxyServer = "",

    [string]$BrowserChannel = "chrome",

    [int]$BrowserManualWaitSeconds = 90,

    [switch]$BrowserStartMinimized,

    [string]$DoclistCacheRoot = "",

    [switch]$SkipRegisterMainFetch,

    [switch]$FetchOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
while ($projectRoot -and -not ((Test-Path -LiteralPath (Join-Path $projectRoot "README.md")) -and (Test-Path -LiteralPath (Join-Path $projectRoot "scripts")))) {
    $parent = Split-Path -Parent $projectRoot
    if ($parent -eq $projectRoot) { break }
    $projectRoot = $parent
}

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Step
    )
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

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

if ($DoclistCacheRoot) {
    if ([System.IO.Path]::IsPathRooted($DoclistCacheRoot)) {
        $doclistCacheRootPath = $DoclistCacheRoot
    } else {
        $doclistCacheRootPath = Join-Path $projectRoot $DoclistCacheRoot
    }
    $cachedCaseDir = Join-Path $doclistCacheRootPath $ApplicationNumber
    foreach ($suffix in @("doclist.csv", "doclist.html")) {
        $cachedPath = Join-Path $cachedCaseDir "$ApplicationNumber-$suffix"
        if (Test-Path -LiteralPath $cachedPath) {
            Copy-Item -LiteralPath $cachedPath -Destination (Join-Path $registerDir "$ApplicationNumber-$suffix") -Force
            Write-Host "[doclist-cache] copied $cachedPath"
        }
    }
}

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
function Invoke-BrowserRegisterFallback {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("main", "doclist")]
        [string]$Tab
    )
    if (-not $BrowserRegisterFallback) {
        throw
    }
    $browserArgs = @(
        (Join-Path $projectRoot "scripts\fetch_epo_doclist_browser.py"),
        "--application-number",
        $ApplicationNumber,
        "--output-dir",
        $registerDir,
        "--profile-dir",
        $BrowserProfileDir,
        "--tab",
        $Tab,
        "--manual-wait-seconds",
        "$BrowserManualWaitSeconds",
        "--use-cached"
    )
    $browserProxy = $BrowserProxyServer
    if (-not $browserProxy) {
        $browserProxy = $EpoProxyUrl
    }
    if ($browserProxy) {
        $browserArgs += @("--proxy-server", $browserProxy)
    }
    if ($BrowserChannel) {
        $browserArgs += @("--browser-channel", $BrowserChannel)
    }
    if ($BrowserStartMinimized) {
        $browserArgs += "--start-minimized"
    }
    python @browserArgs
    Assert-NativeSuccess "Browser register fallback ($Tab)"
}

function Test-DownloadFailureIndex {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Folder
    )
    $failurePath = Join-Path $Folder "download-failures.csv"
    if (-not (Test-Path -LiteralPath $failurePath)) {
        return $false
    }
    try {
        return ((Get-Item -LiteralPath $failurePath).Length -gt 0)
    } catch {
        return $false
    }
}

function Invoke-BrowserDocumentFallback {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OutputDir,

        [Parameter(Mandatory = $true)]
        [string]$TitleRegex,

        [switch]$EarliestPerTitle
    )
    if (-not $BrowserRegisterFallback) {
        return
    }
    $browserArgs = @(
        (Join-Path $projectRoot "scripts\download_epo_docs_browser.py"),
        "--doclist-csv",
        $doclistCsv,
        "--output-dir",
        $OutputDir,
        "--title-regex",
        $TitleRegex,
        "--profile-dir",
        $BrowserProfileDir,
        "--retry-count",
        "$EpoRetryCount",
        "--retry-delay-seconds",
        "$EpoRetryDelaySeconds",
        "--request-delay-milliseconds",
        "$EpoRequestDelayMilliseconds",
        "--request-timeout-seconds",
        "$EpoRequestTimeoutSeconds",
        "--manual-wait-seconds",
        "$BrowserManualWaitSeconds"
    )
    $browserProxy = $BrowserProxyServer
    if (-not $browserProxy) {
        $browserProxy = $EpoProxyUrl
    }
    if ($browserProxy) {
        $browserArgs += @("--proxy-server", $browserProxy)
    }
    if ($BrowserChannel) {
        $browserArgs += @("--browser-channel", $BrowserChannel)
    }
    if ($BrowserStartMinimized) {
        $browserArgs += "--start-minimized"
    }
    if ($EarliestPerTitle) {
        $browserArgs += "--earliest-per-title"
    }
    if ($ContinueOnDownloadError) {
        $browserArgs += "--continue-on-error"
    }
    python @browserArgs
    Assert-NativeSuccess "Browser document fallback"
}

if ($SkipRegisterMainFetch) {
    Write-Host "[register-main] skipped by -SkipRegisterMainFetch"
} else {
    try {
        & (Join-Path $projectRoot "scripts\legacy_register_pipeline\fetch-epo-main.ps1") @fetchArgs
    } catch {
        Write-Warning "Ordinary EPO main fetch failed; trying browser fallback. $($_.Exception.Message)"
        Invoke-BrowserRegisterFallback -Tab "main"
    }
}
if ((Test-Path -LiteralPath $doclistCsv) -and ((Get-Item -LiteralPath $doclistCsv).Length -gt 0)) {
    Write-Host "[doclist-cache] using existing doclist CSV: $doclistCsv"
} else {
    try {
        & (Join-Path $projectRoot "scripts\legacy_register_pipeline\fetch-epo-doclist.ps1") @fetchArgs
    } catch {
        Write-Warning "Ordinary EPO doclist fetch failed; trying browser fallback. $($_.Exception.Message)"
        Invoke-BrowserRegisterFallback -Tab "doclist"
    }
}

Write-Host "[2/8] Downloading benchmark-relevant EPO documents..."
$titleRegex = "^(?!.*translation)(European search opinion|Supplementary European search report|Copy of the international search report|Written opinion of the ISA|Copy of the international preliminary report on patentability|Communication from the Examining Division|Annex to the communication$|Reply to communication from the Examining Division|Amended claims|Claims|Description|Published international application|Text intended for grant|Communication about intention to grant|Decision to grant|.*refus.*|.*withdrawn.*)"
$downloadArgs = @{
    DocListCsv = $doclistCsv
    OutputDir = $docsDir
    TitleRegex = $titleRegex
    RetryCount = $EpoRetryCount
    RetryDelaySeconds = $EpoRetryDelaySeconds
    RequestDelayMilliseconds = $EpoRequestDelayMilliseconds
    RequestTimeoutSeconds = $EpoRequestTimeoutSeconds
}
if ($ContinueOnDownloadError) {
    $downloadArgs["ContinueOnError"] = $true
}
if ($EpoProxyUrl) {
    $downloadArgs["ProxyUrl"] = $EpoProxyUrl
}
& (Join-Path $projectRoot "scripts\legacy_register_pipeline\download-epo-docs.ps1") @downloadArgs
if (Test-DownloadFailureIndex -Folder $docsDir) {
    if ($BrowserRegisterFallback) {
        Write-Warning "Ordinary EPO docs PDF download had failures; trying browser document fallback."
    } else {
        Write-Warning "Ordinary EPO docs PDF download had failures; browser document fallback is disabled."
    }
    Invoke-BrowserDocumentFallback -OutputDir $docsDir -TitleRegex $titleRegex
}

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
    RequestTimeoutSeconds = $EpoRequestTimeoutSeconds
}
if ($ContinueOnDownloadError) {
    $originalDownloadArgs["ContinueOnError"] = $true
}
if ($EpoProxyUrl) {
    $originalDownloadArgs["ProxyUrl"] = $EpoProxyUrl
}
try {
    & (Join-Path $projectRoot "scripts\legacy_register_pipeline\download-epo-docs.ps1") @originalDownloadArgs
} catch {
    if (-not ($FetchOnly -and $ContinueOnDownloadError)) {
        throw
    }
    Write-Warning "Original application download failed in fetch-only mode; continuing because -ContinueOnDownloadError was set. $($_.Exception.Message)"
}
if (Test-DownloadFailureIndex -Folder $originalApplicationDir) {
    if ($BrowserRegisterFallback) {
        Write-Warning "Ordinary original-application PDF download had failures; trying browser document fallback."
    } else {
        Write-Warning "Ordinary original-application PDF download had failures; browser document fallback is disabled."
    }
    Invoke-BrowserDocumentFallback -OutputDir $originalApplicationDir -TitleRegex $originalTitleRegex -EarliestPerTitle
}

if ($FetchOnly) {
    Write-Host "[fetch-only] Stopping after network collection. Text extraction, OCR, benchmark input build, and Markush rendering are local refresh stages."
    Write-Host "Case directory: $caseDir"
    exit 0
}

if ($ExtractPdfText) {
    Write-Host "[4/8] Extracting embedded text from downloaded PDFs before OCR..."
    python (Join-Path $projectRoot "scripts\extract_pdf_text.py") $docsDir
    Assert-NativeSuccess "PDF text extraction"
} elseif ($RunOcr) {
    Write-Host "[4/8] Embedded text extraction skipped. Running OCR directly."
}

if ($RunOcr) {
    Write-Host "[4/8] Running OCR on downloaded PDFs..."
    $ocrArgs = @(
        (Join-Path $projectRoot "scripts\ocr_case_batch.py"),
        $caseDir,
        "--scope",
        $OcrScope,
        "--workers",
        "$OcrWorkers",
        "--timeout",
        "$OcrTimeoutSeconds",
        "--zoom",
        "$OcrZoom"
    )
    if ($OcrIncludeRegex) {
        $ocrArgs += @("--include-regex", $OcrIncludeRegex)
    }
    if ($OcrExcludeRegex) {
        $ocrArgs += @("--exclude-regex", $OcrExcludeRegex)
    }
    python @ocrArgs
    Assert-NativeSuccess "OCR"
} else {
    Write-Host "[4/8] OCR skipped. Use -RunOcr when downloaded PDFs are scanned documents."
}

Write-Host "[5/8] Building benchmark input JSON..."
python (Join-Path $projectRoot "scripts\build_benchmark_input.py") $caseDir --application-number $ApplicationNumber --top-k $TopK -o $benchmarkInput
Assert-NativeSuccess "Benchmark input build"

Write-Host "[6/8] Cropping Markush / Formula candidate images..."
python (Join-Path $projectRoot "scripts\render_markush_pages.py") $benchmarkInput --max-pages 6 --candidate-limit 36 --selected-limit 3 --clear
Assert-NativeSuccess "Markush page rendering"

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
    Assert-NativeSuccess "Benchmark preview refinement"
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
            "--retries",
            "$Retries",
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
    Assert-NativeSuccess "Analysis JSON generation"

    if (($AnalysisMode -eq "split") -and (-not $SkipCompleteSplitAnalysis)) {
        Write-Host "[8b/10] Completing missing split-analysis dimensions if needed..."
        $completeArgs = @(
            (Join-Path $projectRoot "scripts\complete_analysis_json.py"),
            $benchmarkInput,
            $AnalysisJson,
            "--project-root",
            $projectRoot,
            "--env-file",
            $EnvFile,
            "--api-key-env",
            $ApiKeyEnv,
            "--max-source-files",
            "$CompleteMaxSourceFiles",
            "--max-chars-per-file",
            "$CompleteMaxCharsPerFile",
            "--max-field-chars",
            "$CompleteMaxFieldChars",
            "--max-prior-art",
            "$CompleteMaxPriorArt",
            "--max-tokens",
            "$CompleteMaxTokens",
            "--meta-max-tokens",
            "$CompleteMetaMaxTokens",
            "--request-timeout",
            "$CompleteRequestTimeout",
            "--retries",
            "$CompleteRetries",
            "--passes",
            "$CompletePasses",
            "--reasoning-effort",
            $CompleteReasoningEffort,
            "--verbosity",
            $CompleteVerbosity
        )
        if ($Model) {
            $completeArgs += @("--model", $Model)
        }
        if ($BaseUrl) {
            $completeArgs += @("--base-url", $BaseUrl)
        }
        python @completeArgs
        Assert-NativeSuccess "Split analysis completion"
    }

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
    Assert-NativeSuccess "Risk/action translation"
}

if ($AnalysisJson) {
    if (-not $SkipRepairEvidence) {
        Write-Host "[10/13] Repairing report evidence snippets against local OCR/text..."
        python (Join-Path $projectRoot "scripts\repair_report_sources.py") $AnalysisJson --case-dir $caseDir --min-verified-score $VerifyMinScore --min-repair-score $RepairMinScore
        Assert-NativeSuccess "Evidence repair"
    }

    Write-Host "[11/13] Ensuring final HTML fields are complete..."
    python (Join-Path $projectRoot "scripts\ensure_html_field_completeness.py") $AnalysisJson
    Assert-NativeSuccess "HTML field completeness"

    Write-Host "[12/13] Verifying report evidence sources..."
    python (Join-Path $projectRoot "scripts\verify_report_sources.py") $AnalysisJson --case-dir $caseDir --min-score $VerifyMinScore
    if ($LASTEXITCODE -ne 0) {
        if (-not $ContinueOnVerifyError) {
            throw "Evidence source verification failed with exit code $LASTEXITCODE"
        }
        Write-Warning "Evidence source verification failed, continuing because -ContinueOnVerifyError was set."
    }

    Write-Host "[13/14] Downloading prior-art PDFs..."
    python (Join-Path $projectRoot "scripts\download_prior_art_pdfs.py") $benchmarkInput
    Assert-NativeSuccess "Prior-art PDF download"

    Write-Host "[14/14] Rendering benchmark output HTML..."
    $htmlOut = [System.IO.Path]::ChangeExtension($AnalysisJson, ".html")
    python (Join-Path $projectRoot "scripts\json_to_html_report.py") $AnalysisJson -o $htmlOut --benchmark-input $benchmarkInput
    Assert-NativeSuccess "HTML rendering"
    python (Join-Path $projectRoot "scripts\audit_case_quality.py") $caseDir -o (Join-Path $caseDir "_quality_audit.csv")
    Assert-NativeSuccess "Quality audit"
    python (Join-Path $projectRoot "scripts\validate_case_set_completeness.py") $caseDir -o (Join-Path $caseDir "_completeness_validation.csv")
    Assert-NativeSuccess "Completeness validation"
    Write-Host "Benchmark output HTML: $htmlOut"
} else {
    Write-Host "[8/8] No analysis JSON supplied; benchmark output HTML not rendered."
    Write-Host "After LLM analysis, run:"
    Write-Host "python `"$projectRoot\scripts\json_to_html_report.py`" `"<analysis.json>`""
}

Write-Host "Benchmark input JSON: $benchmarkInput"

