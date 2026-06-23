# EP20726962 内容抽查结论

抽查日期：2026-06-17

## 结论

EP20726962 的文件完整性是通过的，但分析内容不能判定为正确，当前应标记为 `needs_review`。

## 发现的问题

1. 首轮生成时，`docs/*.txt` 主要来自 PDF 内嵌文本抽取，但关键审查文件是扫描件，抽取结果基本只有 `--- PAGE ---` 页码标记。
2. 因为源文本不足，原始报告中的 `claim_text.claim_1` 没有真实权利要求内容，多个 `original_text` 只是文件名或 SOURCE 标题，不是审查文件中的连续原文。
3. 运行 `verify_report_sources.py` 后，EP20726962 的多个 `original_text` 字段无法在本地 `.txt` 源文件中匹配。
4. 补做关键文件 OCR 后，`Decision to grant` 文件明确显示：
   - `Decision to grant a European patent pursuant to Article 97(1) EPC`
   - `... is hereby granted ...`
   - `The mention of the grant will be published in European Patent Bulletin 25/29 of 16.07.25`
5. 但是重跑 analysis 时，生成脚本的 source 选择仍未稳定优先纳入最终授权决定，HTML/JSON 仍显示 `outcome: pending`、`grant_label: no`，与本地 OCR 后的授权决定文件不一致。

## 当前可用性

- 可作为“代谢性疾病小分子样本”的原始案卷目录使用。
- 不应直接使用当前 `EP20726962-analysis.json` / `EP20726962-analysis.html` 作为正确审查分析结果。
- 若要进入正式 9 样例集，需要先修复 source 选择策略：最终决定、拟授权通知、审查意见、附件、最新 claims 应优先进入 LLM 上下文。

## 建议

- 对 9 个样例全量运行 `verify_report_sources.py`。
- 对所有扫描 PDF 先跑 key-doc OCR，再构建 benchmark input。
- 修改 `generate_analysis_json_split.py` 的 `collect_source_texts` 逻辑，优先选择：
  1. `Decision to grant` / `Decision to refuse`
  2. `Communication about intention to grant`
  3. `Communication from the Examining Division`
  4. `Annex to the communication`
  5. 最新 `Claims` / `Amended claims`
