param(
    [Parameter(Mandatory = $true)]
    [string]$DocListCsv,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$TitleRegex = "Communication|Annex|Decision|search report|search opinion|Summons|Minutes|intention to grant",

    [switch]$DownloadAll,

    [switch]$EarliestPerTitle,

    [switch]$ContinueOnError,

    [int]$RetryCount = 4,

    [int]$RetryDelaySeconds = 3,

    [int]$RequestDelayMilliseconds = 1200,

    [int]$RequestTimeoutSeconds = 60,

    [string]$ProxyUrl = $env:EPO_PROXY_URL
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

function Convert-ToSafeName([string]$name) {
    $safe = $name -replace '[\\/:*?"<>|]', '_'
    $safe = $safe -replace '\s+', '_'
    if ($safe.Length -gt 120) {
        $safe = $safe.Substring(0, 120)
    }
    return $safe
}

function Invoke-EpoRequestWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$OutFile,

        [Parameter(Mandatory = $true)]
        [hashtable]$Headers
    )

    $attempt = 0
    while ($true) {
        $attempt += 1
        try {
            $requestArgs = @{
                Uri = $Uri
                OutFile = $OutFile
                UseBasicParsing = $true
                Headers = $Headers
                TimeoutSec = $RequestTimeoutSeconds
            }
            if ($ProxyUrl) {
                $requestArgs["Proxy"] = $ProxyUrl
            }
            Invoke-WebRequest @requestArgs
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

function Test-ValidPdfFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path))
        return ($bytes.Length -ge 5 -and $bytes[0] -eq 0x25 -and $bytes[1] -eq 0x50 -and $bytes[2] -eq 0x44 -and $bytes[3] -eq 0x46)
    } catch {
        return $false
    }
}

$rows = @(Import-Csv -LiteralPath $DocListCsv)
if (-not $DownloadAll) {
    $rows = @($rows | Where-Object { $_.title -match $TitleRegex })
}
if (-not $rows -or $rows.Count -eq 0) {
    throw "No documents matched TitleRegex: $TitleRegex"
}

function Get-DateKey([string]$value) {
    if ($value -match '^([0-9]{2})\.([0-9]{2})\.([0-9]{4})$') {
        return "$($matches[3])-$($matches[2])-$($matches[1])"
    }
    return $value
}

function Get-TitleKey([string]$value) {
    return (($value -replace '\s+', ' ').Trim().ToLowerInvariant())
}

if ($EarliestPerTitle) {
    $rows = @(
        $rows |
            Group-Object { Get-TitleKey $_.title } |
            ForEach-Object {
                $_.Group | Sort-Object @{ Expression = { Get-DateKey $_.date } }, documentId | Select-Object -First 1
            } |
            Sort-Object @{ Expression = { Get-DateKey $_.date } }, title
    )
}

$manifest = @()
$failures = @()
foreach ($row in $rows) {
    $app = $row.applicationNumber
    if ($app -notmatch '^EP') {
        $app = "EP$app"
    }

    $date = ($row.date -replace '\.', '-')
    $title = Convert-ToSafeName $row.title
    $fileName = "$date`_$title`_$($row.documentId).pdf"
    $out = Join-Path $OutputDir $fileName

    $url = "https://register.epo.org/application?documentId=$($row.documentId)&appnumber=$app&showPdfPage=all&proc="
    if (Test-ValidPdfFile -Path $out) {
        Write-Host "Using cached PDF $($row.date) - $($row.title)"
        $manifest += [pscustomobject]@{
            applicationNumber = $app
            documentId = $row.documentId
            date = $row.date
            title = $row.title
            phase = $row.phase
            pages = $row.pages
            fileName = $fileName
            path = $out
            url = $url
        }
        continue
    }

    Write-Host "Downloading $($row.date) - $($row.title)"
    try {
        Invoke-EpoRequestWithRetry -Uri $url -OutFile $out -Headers @{
            "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            "Accept" = "application/pdf,*/*"
        }
        if (-not (Test-ValidPdfFile -Path $out)) {
            Remove-Item -LiteralPath $out -Force
            throw "Downloaded content is not a PDF for documentId=$($row.documentId). URL: $url"
        }
        $manifest += [pscustomobject]@{
            applicationNumber = $app
            documentId = $row.documentId
            date = $row.date
            title = $row.title
            phase = $row.phase
            pages = $row.pages
            fileName = $fileName
            path = $out
            url = $url
        }
    } catch {
        $message = $_.Exception.Message
        Write-Warning "Failed to download documentId=$($row.documentId): $message"
        $failures += [pscustomobject]@{
            applicationNumber = $app
            documentId = $row.documentId
            date = $row.date
            title = $row.title
            phase = $row.phase
            pages = $row.pages
            fileName = $fileName
            path = $out
            url = $url
            error = $message
        }
        if (-not $ContinueOnError) {
            throw
        }
    }
    Start-Sleep -Milliseconds $RequestDelayMilliseconds
}

$manifestPath = Join-Path $OutputDir "download-index.csv"
$manifest | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8
$failuresPath = Join-Path $OutputDir "download-failures.csv"
if ($failures.Count -gt 0) {
    $failures | Export-Csv -LiteralPath $failuresPath -NoTypeInformation -Encoding UTF8
} elseif (Test-Path -LiteralPath $failuresPath) {
    Remove-Item -LiteralPath $failuresPath -Force
}

Write-Host "Downloaded $($manifest.Count)/$($rows.Count) document(s) to $OutputDir"
if ($failures.Count -gt 0) {
    Write-Warning "Failed $($failures.Count) document(s). Saved failure index: $failuresPath"
}
Write-Host "Saved download index: $manifestPath"
