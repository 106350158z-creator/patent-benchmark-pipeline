param(
    [Parameter(Mandatory = $true)]
    [string]$DocListCsv,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$TitleRegex = "Communication|Annex|Decision|search report|search opinion|Summons|Minutes|intention to grant",

    [switch]$DownloadAll
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

$rows = Import-Csv -LiteralPath $DocListCsv
if (-not $DownloadAll) {
    $rows = $rows | Where-Object { $_.title -match $TitleRegex }
}

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
    Write-Host "Downloading $($row.date) - $($row.title)"
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -Headers @{
        "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
        "Accept" = "application/pdf,*/*"
    }
    Start-Sleep -Milliseconds 500
}

Write-Host "Downloaded $($rows.Count) document(s) to $OutputDir"
