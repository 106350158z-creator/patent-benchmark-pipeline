# 疾病分类 9 个样例复核记录

复核日期：2026-06-17

## 结论

这 9 个样例的产物完整性通过复核：每个 case 均有 benchmark input、analysis JSON、analysis HTML、register HTML/CSV、审查文件 PDF、审查文件 TXT、original-application PDF。所有 JSON 可解析，抽查 PDF 文件头均为 `%PDF`，HTML 中能匹配到对应 application number 和发明名称。

但内容正确性需要单独复核。2026-06-17 抽查 `EP20726962` 后发现，该 case 的原始案卷材料齐全，但 analysis JSON/HTML 不能直接视为正确结果：关键扫描 PDF 首轮没有 OCR，导致 claim/evidence 文本不足；补做关键 OCR 后又发现 `Decision to grant` 与报告中的 `pending/no` 结论不一致。详见 `EP20726962_content_review.md`。

## 分类复核

| 疾病类别 | Application | Publication | 分类依据 |
|---|---|---|---|
| 肿瘤 | EP16802532 | EP3312180B1 | EGFR inhibitor，肿瘤靶向小分子 |
| 肿瘤 | EP17191704 | EP3311818A3 | BTK inhibitor for solid tumors，肿瘤靶向小分子 |
| 肿瘤 | EP17821262 | EP3478286B1 | Ovarian cancer / PARP inhibitor 相关 |
| 自身免疫性疾病 | EP18755728 | EP3661921B1 | NLRP3 inflammasome inhibitor，炎症/自免相关 |
| 自身免疫性疾病 | EP18826609 | EP3728238B1 | NLRP3 inflammasome modulator，炎症/自免相关 |
| 自身免疫性疾病 | EP12710797 | EP2685976B1 | Quinolone analogs for treating autoimmune diseases |
| 代谢性疾病 | EP16823054 | EP3397631B1 | Ketohexokinase inhibitors，代谢疾病相关 |
| 代谢性疾病 | EP18712343 | EP3589636B1 | ACC inhibitors and solid forms，脂质/代谢相关 |
| 代谢性疾病 | EP20726962 | EP3972596B1 | GLP-1R agonist / NASH-NAFLD 相关 |

## 注意事项

- `keyword_group` 是候选池的检索来源标签，不等于疾病分类标签。代谢性疾病 3 个样例在候选池里显示为 `SQLE`，但本次分类依据是题名、专利主题和小分子疾病语义。
- `EP17191704` 是 `negative_ungranted_candidate`，适合作为肿瘤方向的未授权/撤回候选样例；如果后续要求每类 3 个都必须是授权正样本，可替换为同目录已有的 EGFR/PARP/BTK 授权样本。
- 本次范围刻意排除了抗体、ADC、核酸药物、基因/细胞治疗。样例选择按小分子专利主题复核。

## 产物状态

- Analysis JSON：9/9
- Analysis HTML：9/9
- Benchmark input：9/9
- Register HTML/CSV：9/9
- 审查文件 PDF：全部 case 均有
- 审查文件 TXT：全部 case 均已补齐 PDF 内嵌文本抽取结果
- Original application PDF：全部 case 均有
