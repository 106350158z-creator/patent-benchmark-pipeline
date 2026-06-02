param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationNumber,

    [string]$OutputDir = "."
)

$ErrorActionPreference = "Stop"

if ($ApplicationNumber -notmatch '^EP') {
    $ApplicationNumber = "EP$ApplicationNumber"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$htmlPath = Join-Path $OutputDir "$ApplicationNumber-doclist.html"
$csvPath = Join-Path $OutputDir "$ApplicationNumber-doclist.csv"
$url = "https://register.epo.org/application?number=$ApplicationNumber&lng=en&tab=doclist"

Invoke-WebRequest -Uri $url -OutFile $htmlPath -UseBasicParsing
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

$rows | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8
Write-Host "Saved HTML: $htmlPath"
Write-Host "Saved CSV : $csvPath"
Write-Host "Rows      : $($rows.Count)"

