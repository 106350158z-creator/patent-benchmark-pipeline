param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationNumber,

    [string]$OutputRoot = ".\runs",

    [string]$AnalysisJson = "",

    [switch]$GenerateAnalysis,

    [string]$EnvFile = ".env",

    [string]$Model = "",

    [string]$BaseUrl = "",

    [string]$ApiKeyEnv = "OHMYGPT_API_KEY",

    [switch]$RunOcr,

    [int]$TopK = 20
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if ($ApplicationNumber -notmatch '^EP') {
    $ApplicationNumber = "EP$ApplicationNumber"
}

$caseDir = Join-Path $OutputRoot $ApplicationNumber
$docsDir = Join-Path $caseDir "docs"
$doclistCsv = Join-Path $caseDir "$ApplicationNumber-doclist.csv"
$benchmarkInput = Join-Path $caseDir "$ApplicationNumber-benchmark-input.json"

New-Item -ItemType Directory -Force -Path $caseDir | Out-Null

Write-Host "[1/5] Fetching EPO register pages..."
& (Join-Path $projectRoot "scripts\fetch-epo-main.ps1") -ApplicationNumber $ApplicationNumber -OutputDir $caseDir
& (Join-Path $projectRoot "scripts\fetch-epo-doclist.ps1") -ApplicationNumber $ApplicationNumber -OutputDir $caseDir

Write-Host "[2/5] Downloading benchmark-relevant EPO documents..."
$titleRegex = "European search opinion|Supplementary European search report|Communication from the Examining Division|Annex to the communication$|Reply to communication from the Examining Division|Amended claims|Claims|Description|Published international application|Text intended for grant|Communication about intention to grant|Decision to grant|refus|withdrawn"
& (Join-Path $projectRoot "scripts\download-epo-docs.ps1") -DocListCsv $doclistCsv -OutputDir $docsDir -TitleRegex $titleRegex

if ($RunOcr) {
    Write-Host "[3/5] Running OCR on downloaded PDFs..."
    python (Join-Path $projectRoot "scripts\ocr-pdfs.py") $docsDir --zoom 1.6 --overwrite
} else {
    Write-Host "[3/5] OCR skipped. Use -RunOcr when downloaded PDFs are scanned documents."
}

Write-Host "[4/5] Building benchmark input JSON..."
python (Join-Path $projectRoot "scripts\build_benchmark_input.py") $caseDir --application-number $ApplicationNumber --top-k $TopK -o $benchmarkInput

if ($GenerateAnalysis) {
    Write-Host "[5/6] Generating analysis JSON via LLM..."
    if (-not $AnalysisJson) {
        $AnalysisJson = Join-Path $caseDir "$ApplicationNumber-analysis.json"
    }

    $llmArgs = @(
        (Join-Path $projectRoot "scripts\generate_analysis_json.py"),
        $benchmarkInput,
        "-o",
        $AnalysisJson,
        "--env-file",
        $EnvFile,
        "--api-key-env",
        $ApiKeyEnv
    )
    if ($Model) {
        $llmArgs += @("--model", $Model)
    }
    if ($BaseUrl) {
        $llmArgs += @("--base-url", $BaseUrl)
    }
    python @llmArgs
}

if ($AnalysisJson) {
    Write-Host "[6/6] Rendering benchmark output HTML..."
    $htmlOut = [System.IO.Path]::ChangeExtension($AnalysisJson, ".html")
    python (Join-Path $projectRoot "scripts\json_to_html_report.py") $AnalysisJson -o $htmlOut
    Write-Host "Benchmark output HTML: $htmlOut"
} else {
    Write-Host "[5/5] No analysis JSON supplied; benchmark output HTML not rendered."
    Write-Host "After LLM analysis, run:"
    Write-Host "python `"$projectRoot\scripts\json_to_html_report.py`" `"<analysis.json>`""
}

Write-Host "Benchmark input JSON: $benchmarkInput"
