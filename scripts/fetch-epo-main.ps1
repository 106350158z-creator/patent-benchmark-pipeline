param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationNumber,

    [string]$OutputDir = ".",

    [int]$RetryCount = 4,

    [int]$RetryDelaySeconds = 3,

    [string]$ProxyUrl = $env:EPO_PROXY_URL,

    [switch]$UseCached
)

$ErrorActionPreference = "Stop"

if ($ApplicationNumber -notmatch '^EP') {
    $ApplicationNumber = "EP$ApplicationNumber"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$htmlPath = Join-Path $OutputDir "$ApplicationNumber-main.html"
$url = "https://register.epo.org/application?number=$ApplicationNumber&lng=en&tab=main"

function Test-EpoRejectedHtml {
    param([string]$Html)
    return $Html -match '(?i)has rejected your request|rejected your request|restrictedrequest|just a moment|__cf_chl|cf-chl-widget|challenge-form'
}

function Test-UsableEpoHtmlFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $html = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($html)) {
        return $false
    }
    return -not (Test-EpoRejectedHtml -Html $html)
}

if ($UseCached -and (Test-UsableEpoHtmlFile -Path $htmlPath)) {
    Write-Host "Using cached main page: $htmlPath"
    exit 0
}

function Invoke-EpoHtmlWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$OutFile
    )
    $attempt = 0
    while ($true) {
        $attempt += 1
        try {
            $requestArgs = @{
                Uri = $Uri
                OutFile = $OutFile
                UseBasicParsing = $true
                Headers = @{
                    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
                    "Accept" = "text/html,*/*"
                }
                TimeoutSec = 120
            }
            if ($ProxyUrl) {
                $requestArgs["Proxy"] = $ProxyUrl
            }
            Invoke-WebRequest @requestArgs
            $html = Get-Content -LiteralPath $OutFile -Raw -Encoding UTF8
            if (Test-EpoRejectedHtml -Html $html) {
                throw "EPO returned a challenge page instead of register HTML."
            }
            return
        } catch {
            if ($attempt -gt $RetryCount) {
                throw
            }
            $sleepSeconds = $RetryDelaySeconds * $attempt
            Write-Warning "Request failed (attempt $attempt/$RetryCount). Retrying in $sleepSeconds second(s): $Uri"
            Start-Sleep -Seconds $sleepSeconds
        }
    }
}

Invoke-EpoHtmlWithRetry -Uri $url -OutFile $htmlPath

$sanitizeScript = Join-Path (Split-Path -Parent $PSCommandPath) "sanitize_epo_html.py"
if (Test-Path -LiteralPath $sanitizeScript) {
    python $sanitizeScript $htmlPath --in-place
}

Write-Host "Saved main page: $htmlPath"
