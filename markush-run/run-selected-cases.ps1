param(
    [string]$Root = "C:\Users\de'l'l\Desktop\epo-report-analysis",
    [string[]]$Applications = @("EP18885399", "EP08863620")
)

$ErrorActionPreference = "Stop"

$cases = @{
    "EP18885399" = @{
        Name = "EP18885399_granted"
        Regex = "Communication from the Examining Division|Annex to the communication$|European search opinion|Supplementary European search report|Communication about intention to grant|Decision to grant a European patent|Text intended for grant"
    }
    "EP08863620" = @{
        Name = "EP08863620_withdrawn"
        Regex = "Communication from the Examining Division|Annex to the communication$|European search opinion|Supplementary European search report|Application deemed to be withdrawn|Amended claims|Claims|Description"
    }
}

foreach ($app in $Applications) {
    if (-not $cases.ContainsKey($app)) {
        throw "Unknown preset application: $app"
    }

    $case = $cases[$app]
    $caseDir = Join-Path $Root ("markush-run\" + $case.Name)
    $docsDir = Join-Path $caseDir "docs"
    $docList = Join-Path $caseDir "$app-doclist.csv"

    & (Join-Path $Root "scripts\fetch-epo-doclist.ps1") -ApplicationNumber $app -OutputDir $caseDir
    & (Join-Path $Root "scripts\download-epo-docs.ps1") -DocListCsv $docList -OutputDir $docsDir -TitleRegex $case.Regex

    Write-Host "OCR key documents manually when needed, for example:"
    Write-Host "python `"$Root\scripts\ocr-pdfs.py`" `"$docsDir\<document>.pdf`" --zoom 1.6 --overwrite"
}

