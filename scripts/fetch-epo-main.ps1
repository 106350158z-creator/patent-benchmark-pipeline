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

$htmlPath = Join-Path $OutputDir "$ApplicationNumber-main.html"
$url = "https://register.epo.org/application?number=$ApplicationNumber&lng=en&tab=main"

Invoke-WebRequest -Uri $url -OutFile $htmlPath -UseBasicParsing -Headers @{
    "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    "Accept" = "text/html,*/*"
}

Write-Host "Saved main page: $htmlPath"

