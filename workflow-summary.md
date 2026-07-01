# EPO Benchmark 脚本变动与最新运行入口

本文档覆盖旧版流程说明，记录当前 `scripts/` 的最新职责划分和推荐运行方式。以后运行程序时，以这里和 `README.md` 中的入口为准。

## 最新推荐入口

### 单个 EP 案例

单案例继续使用 `scripts/run_epo_benchmark.ps1`。当前版本不只是抓取、OCR、生成分析和渲染 HTML，还会在生成报告后自动执行证据修复、字段补齐、证据校验、质量审计和完整性校验。

```powershell
.\scripts\run_epo_benchmark.ps1 `
  -ApplicationNumber EP18885399 `
  -OutputRoot markush-run\benchmark `
  -RunOcr `
  -GenerateAnalysis `
  -AnalysisMode split `
  -EnvFile .env `
  -ApiKeyEnv OPENAI_API_KEY `
  -BaseUrl https://yunwu.ai/v1 `
  -Model gpt-5.5 `
  -WriteAnalysisSteps
```

默认会执行：

- `repair_report_sources.py`：修复 analysis JSON 中较弱的 `original_text`，尽量替换成本地 TXT 中可匹配的连续原文。
- `ensure_html_field_completeness.py`：补齐 HTML 会展示的关键字段，避免报告渲染后出现空块。
- `verify_report_sources.py`：验证报告证据原文是否真的来自本地文本。
- `audit_case_quality.py`：输出 `_quality_audit.csv`。
- `validate_case_set_completeness.py`：输出 `_completeness_validation.csv`。

只有在明确需要跳过证据修复时才使用 `-SkipRepairEvidence`。证据校验失败默认会中断流程；如果只是批量试跑，可加 `-ContinueOnVerifyError` 继续执行。

### 已有案例集或嵌套目录

对已有目录批量刷新，使用新增的 `scripts/run_case_set_refresh.py`。它可以处理单个 case，也可以递归发现嵌套目录中的 `EP*` case。

```powershell
python scripts\run_case_set_refresh.py `
  疾病分类9个样例 `
  --stage all `
  --env-file .env `
  --api-key-env OPENAI_API_KEY `
  --base-url https://yunwu.ai/v1 `
  --model gpt-5.5 `
  --write-analysis-steps
```

`--stage prepare` 只做本地准备，包括 PDF 文本提取、OCR、claims review、benchmark input 和 Markush 图片；`--stage analysis` 在已有 input 上生成 analysis、修复证据、补齐字段、验证来源并渲染 HTML；`--stage all` 连续执行两段。

## 本次脚本层面的核心变动

### 1. 主流程更严格

`scripts/run_epo_benchmark.ps1` 增加了统一的原生命令失败检查。PDF 文本提取、OCR、benchmark input 构建、Markush 页面渲染、分析生成、翻译、证据修复、字段补齐、HTML 渲染、质量审计和完整性校验，只要关键步骤失败就会及时中断。

下载范围也扩展了，文档标题匹配现在包含 `Copy of the international search report`、`Written opinion of the ISA`、`Copy of the international preliminary report on patentability` 等 PCT/ISA 相关材料。

### 2. Benchmark input 优先使用核验权利要求

`scripts/build_benchmark_input.py` 现在会优先读取 `<EP>-claims-verified.json` 中状态为 `verified` 的权利要求，并把它作为 `benchmark_input.claim_text`。如果没有核验文件，才回退到 OCR preview。

同时，`source_trace` 改为以 case 目录为基准的相对路径，不再把本机绝对路径写进 benchmark input。它还会记录输入质量信息，例如 claim 1 的有效字符数、source 文档数量和 source 有效字符数。

### 3. 生成分析前增加 source quality gate

`scripts/generate_analysis_json.py` 和 `scripts/generate_analysis_json_split.py` 都增加了输入质量检查：

- claim 1 必须有足够的有效文本。
- SOURCE 文档不能为空。
- SOURCE 文本不能过短。
- 默认不再只用 Register HTML 作为 SOURCE。

如确实需要放行低质量输入，显式加 `--allow-low-quality-source`。如需要把 Register HTML 作为 SOURCE，可加 `--include-register-html`。

拆分式分析的默认 `--max-source-files` 从 3 提高到 8，并增加 `--retries`。每个维度如果缺少分数或 `original_text`，会重试对应子请求。

### 4. 拆分式分析会生成风险/动作依据原文

`scripts/generate_analysis_json_split.py` 会从维度分数和审查材料证据中派生：

- `risk_source_sentences`
- `action_basis_source_sentences`

这两个字段用于 HTML 报告中展示带来源、原文、翻译和证据说明的风险原因/建议依据，而不是只展示普通中文列表。

### 5. HTML 报告更强调可核验材料

`scripts/json_to_html_report.py` 的报告标题改为 `Patent Examination Benchmark Report`。报告预览区会优先读取 `<EP>-claims-verified.json`：

- 如果所有 claim 都是 `verified`，展示 `Verified Claims`。
- 如果还未全部核验，展示 draft/OCR 预览，并给出 claims review HTML、claims JSON、authority PDF 的链接。

风险和建议区也改为优先渲染 `risk_source_sentences` / `action_basis_source_sentences`，展示原文、翻译、来源和说明。旧字段 `top_risk_reasons` / `recommended_actions` 只作为 fallback。

### 6. Markush 图片提取带状态说明

`scripts/render_markush_pages.py` 会写入 `drug_structure.markush_extraction_status`，说明当前 Markush/Formula 图片提取处于哪种状态：

- `selected`
- `candidates_rejected`
- `pages_no_candidates`
- `snippets_no_pages`
- `no_formula_context`

这样 HTML 或后续审计能区分“没有 Markush 上下文”和“有候选但过滤后未通过”。

### 7. OCR 批处理支持嵌套 case

`scripts/ocr_case_batch.py` 现在会递归发现嵌套目录中的 `EP*` case，并用有效字符数判断 OCR 文本是否可用，而不只是检查文件大小。

### 8. 新增质量、完整性和证据修复脚本

新增脚本职责如下：

| 脚本 | 作用 |
| --- | --- |
| `scripts/run_case_set_refresh.py` | 批量刷新已有 case set，串联 OCR、claims review、benchmark input、analysis、修复、验证、HTML 和审计 |
| `scripts/generate_claims_verified.py` | 从本地 EPO claim PDF 生成 claims review HTML 和 claims verified JSON 草稿 |
| `scripts/repair_report_sources.py` | 用本地 TXT 中的连续片段修复 analysis JSON 的 `original_text` |
| `scripts/ensure_html_field_completeness.py` | 保证最终 analysis JSON 中有 HTML 渲染所需字段 |
| `scripts/verify_report_sources.py` | 验证所有 `original_text` 是否可在本地 TXT 中匹配 |
| `scripts/audit_case_quality.py` | 审计 case 质量、claims review 状态和证据可追溯性 |
| `scripts/validate_case_set_completeness.py` | 校验 PDF/TXT/JSON/HTML/claims verified 等完整性 |

## 最新样例集

本次提交新增 `疾病分类9个样例/`，包含 9 个 EP case：

| 分类 | 案例 |
| --- | --- |
| 代谢性疾病 | `EP16823054`, `EP18712343`, `EP20726962` |
| 肿瘤 | `EP16802532`, `EP17191704`, `EP17821262` |
| 自身免疫性疾病 | `EP12710797`, `EP18755728`, `EP18826609` |

每个 case 目录通常包含 register、docs、original-application、benchmark input、analysis JSON/HTML、claims review、claims verified JSON、OCR 状态、质量审计和完整性校验结果。

## 运行时注意

- 默认优先走拆分式分析 `-AnalysisMode split` / `generate_analysis_json_split.py`。
- 不要把低质量 OCR 或仅 Register HTML 的输入直接送入分析，除非显式使用 `--allow-low-quality-source`。
- 如果要复用已有 case set，不要重新写临时流程，优先使用 `run_case_set_refresh.py`。
- 报告中的 `original_text` 必须能追溯到本地 TXT 连续片段；生成后应保留 `verify_report_sources.py` 和 `audit_case_quality.py` 的结果。
