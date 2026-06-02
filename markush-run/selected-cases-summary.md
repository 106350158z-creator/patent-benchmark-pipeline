# 选中案件与抓取结果

## 选中正例

- EP3720438B1 / EP18885399.8
- 授权状态：granted；EPO Register 显示 2023-08-03 Decision to grant，2024-07-05 No opposition filed within time limit。
- 技术匹配：与本地 APX001/APX001A/fosmanogepix、GWT1、heterocycle substituted pyridine derivative 资料最贴合。
- 抓取文档：doclist、main page、European search opinion、Supplementary search report、Examining Division communication、Annex、Rule 71(3) 通信、grant text。
- 输出 JSON：EP3720438_granted_analysis.json

## 选中反例

- EP2226320A4 / EP08863620.4
- 授权状态：not granted；EPO Register 显示 Application deemed to be withdrawn，2014-08-01 因未缴续展费视为撤回。
- 技术匹配：同一 Eisai 杂环取代吡啶抗真菌链路的制备方法案，有检索意见和实审意见，可分析 Art.82 单一性和 D40 创造性判断。
- 抓取文档：doclist、main page、European search opinion、Supplementary search report、Examining Division communication、Annex、amended claims、withdrawal notice。
- 输出 JSON：EP2226320_withdrawn_analysis.json

## 复用命令

```powershell
$root = "C:\Users\de'l'l\Desktop\epo-report-analysis"
& "$root\scripts\fetch-epo-doclist.ps1" -ApplicationNumber EP18885399 -OutputDir "$root\markush-run\EP18885399_granted"
& "$root\scripts\download-epo-docs.ps1" -DocListCsv "$root\markush-run\EP18885399_granted\EP18885399-doclist.csv" -OutputDir "$root\markush-run\EP18885399_granted\docs" -TitleRegex "Communication from the Examining Division|Annex to the communication$|European search opinion|Supplementary European search report|Communication about intention to grant|Decision to grant a European patent|Text intended for grant"
python "$root\scripts\ocr-pdfs.py" "$root\markush-run\EP18885399_granted\docs\19-05-2021_European_search_opinion_E57605779826DSU.pdf" --zoom 1.6 --overwrite
```

