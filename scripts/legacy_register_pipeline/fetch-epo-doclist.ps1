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

$htmlPath = Join-Path $OutputDir "$ApplicationNumber-doclist.html"
$csvPath = Join-Path $OutputDir "$ApplicationNumber-doclist.csv"
$url = "https://register.epo.org/application?number=$ApplicationNumber&lng=en&tab=doclist"
$headers = @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    "Accept" = "text/html,*/*"
}

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

function Test-UsableDocListCsv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $cachedRows = @(Import-Csv -LiteralPath $Path)
        return $cachedRows.Count -gt 0
    } catch {
        return $false
    }
}

if ($UseCached -and (Test-UsableEpoHtmlFile -Path $htmlPath) -and (Test-UsableDocListCsv -Path $csvPath)) {
    Write-Host "Using cached doclist: $csvPath"
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
                Headers = $headers
                TimeoutSec = 120
            }
            if ($ProxyUrl) {
                $requestArgs["Proxy"] = $ProxyUrl
            }
            Invoke-WebRequest @requestArgs
            $html = Get-Content -LiteralPath $OutFile -Raw -Encoding UTF8
            if (Test-EpoRejectedHtml -Html $html) {
                throw "EPO returned a challenge page instead of doclist HTML."
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
$html = Get-Content -LiteralPath $htmlPath -Raw -Encoding UTF8

$pattern = '(?s)<tr>\s*<td class="smallBorder">.*?<input[^>]+value="([^"]+)".*?</td>\s*<td>([^<]+)</td>\s*<td class="nowrap"><a id="[^"]+".*?>(.*?)</a></td>\s*<td>(.*?)</td>\s*<td class="right noOfPages">([^<]+)</td>'
$rows = [regex]::Matches($html, $pattern) | ForEach-Object {
    [pscustomobject]@{
        applicationNumber = $ApplicationNumber
        documentId = $_.Groups[1].Value
        date = [System.Net.WebUtility]::HtmlDecode($_.Groups[2].Value.Trim())
        title = [System.Net.WebUtility]::HtmlDecode(($_.Groups[3].Value -replace '<.*?>', '').Trim())
        phase = [System.Net.WebUtility]::HtmlDecode(($_.Groups[4].Value -replace '<.*?>', ' ' -replace '&nbsp;', ' ').Trim())
        pages = [int]($_.Groups[5].Value.Trim())
    }
}

if ($rows.Count -eq 0) {
    throw "No document rows parsed from $htmlPath. EPO markup may have changed, or the application number is invalid."
}

$rows | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8

$sanitizeScript = Join-Path (Split-Path -Parent $PSCommandPath) "sanitize_epo_html.py"
if (Test-Path -LiteralPath $sanitizeScript) {
    python $sanitizeScript $htmlPath --in-place
}

Write-Host "Saved HTML: $htmlPath"
Write-Host "Saved CSV : $csvPath"
Write-Host "Rows      : $($rows.Count)"
