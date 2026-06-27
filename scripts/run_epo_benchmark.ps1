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

    [int]$MaxSourceFiles = 6,

    [int]$MaxCharsPerFile = 2600,

    [int]$MaxFieldChars = 2200,

    [int]$MaxPriorArt = 8,

    [int]$MaxTokens = 1200,

    [int]$MetaMaxTokens = 800,

    [int]$RequestTimeout = 180,

    [string]$ReasoningEffort = "low",

    [string]$Verbosity = "low",

    [switch]$WriteAnalysisSteps,

    [switch]$SkipRepairEvidence,

    [switch]$ContinueOnVerifyError,

    [double]$RepairMinScore = 0.55,

    [double]$VerifyMinScore = 0.88,

    [int]$EpoRetryCount = 4,

    [int]$EpoRetryDelaySeconds = 3,

    [int]$EpoRequestDelayMilliseconds = 1200,

    [string]$EpoProxyUrl = $env:EPO_PROXY_URL
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

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
$titleRegex = "^(?!.*translation)(European search opinion|Supplementary European search report|Copy of the international search report|Written opinion of the ISA|Copy of the international preliminary report on patentability|Communication from the Examining Division|Annex to the communication$|Reply to communication from the Examining Division|Amended claims|Claims|Description|Published international application|Text intended for grant|Communication about intention to grant|Decision to grant|.*refus.*|.*withdrawn.*)"
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
