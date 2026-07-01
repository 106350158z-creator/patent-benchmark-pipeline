# EPO Patent Benchmark Pipeline

这个仓库用于从 EPO Register 抓取欧洲专利申请材料，整理 benchmark input，调用 OpenAI-compatible LLM 生成审查分析 JSON，并渲染为 HTML 报告。

当前脚本职责、质量校验和最新推荐运行入口见 `workflow-summary.md`。后续运行程序时，以 `workflow-summary.md` 和本文档中的入口命令为准。

远程仓库：

```text
git@github.com:106350158z-creator/patent-benchmark-pipeline.git
```

## 功能

- 抓取 EPO Register main page 和 doclist
- 下载检索意见、审查意见、答复、修改权利要求、授权文本等关键 PDF
- 可选 OCR，将 PDF 转为文本
- 生成 benchmark input JSON
- 调用 LLM 生成专利审查分析 JSON，支持单次主分析和拆分式多次调用
- 将分析 JSON 渲染为 HTML 报告

## 环境准备

建议使用 Python 3.10+。

```powershell
pip install -r requirements.txt
```

如需 OCR，另装 OCR 依赖：

```powershell
pip install -r scripts\requirements-ocr.txt
```

复制环境变量模板并填写 API key：

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env` 已加入 `.gitignore`，不会提交到仓库。

## 一键运行

抓取材料、OCR，并生成 benchmark input：

```powershell
.\scripts\run_epo_benchmark.ps1 `
  -ApplicationNumber EP18885399 `
  -OutputRoot markush-run\benchmark `
  -RunOcr
```

抓取材料、OCR、生成 benchmark input、调用 LLM，并渲染 HTML：

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

当前推荐使用拆分式主分析。该模式会把一次完整审查分析拆成 `meta`、`novelty`、`inventive_step`、`support`、`clarity`、`eligibility` 六次较小调用，再本地合并为同一结构的 analysis JSON。报告生成后会继续执行证据修复、HTML 字段补齐、证据校验、质量审计和完整性校验。

如需对已有 case set 或嵌套目录批量刷新，使用 `scripts/run_case_set_refresh.py`：

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

输出目录默认位于：

```text
markush-run\benchmark\<ApplicationNumber>\
```

默认 case 目录结构：

```text
markush-run\benchmark\<ApplicationNumber>\
  register\                 # EPO Register main/doclist HTML 和 doclist CSV
  docs\                     # 审查链路使用的 PDF/OCR/TXT
  original-application\     # 原始申请文件 PDF 与 download-index.csv
  <ApplicationNumber>-benchmark-input.json
  <ApplicationNumber>-analysis.json
  <ApplicationNumber>-analysis.html
```

生成 HTML 时，`original-application\` 中的原始申请文件会通过 `benchmark-input.json` 的 `source_trace.original_application_files` 写入报告链接。

## 分步运行

如果目录中已经有 `*-main.html`、`*-doclist.csv` 和 `docs/*.txt`，可以直接生成 benchmark input：

```powershell
python scripts\build_benchmark_input.py `
  markush-run\benchmark\EP18885399 `
  --application-number EP18885399 `
  -o markush-run\benchmark\EP18885399\EP18885399-benchmark-input.json
```

生成分析 JSON：

```powershell
python scripts\generate_analysis_json.py `
  markush-run\benchmark\EP18885399\EP18885399-benchmark-input.json `
  -o markush-run\benchmark\EP18885399\EP18885399-analysis.json
```

生成拆分式分析 JSON：

```powershell
python scripts\generate_analysis_json_split.py `
  markush-run\benchmark\EP21842292\EP21842292-benchmark-input.json `
  -o markush-run\benchmark\EP21842292\EP21842292-analysis.split-gpt55.json `
  --env-file .env `
  --api-key-env OPENAI_API_KEY `
  --base-url https://yunwu.ai/v1 `
  --model gpt-5.5 `
  --max-source-files 3 `
  --max-chars-per-file 1800 `
  --max-field-chars 1800 `
  --max-prior-art 8 `
  --max-tokens 1000 `
  --meta-max-tokens 600 `
  --request-timeout 180 `
  --reasoning-effort low `
  --verbosity low `
  --write-steps
```

只生成 prompt，不调用 API：

```powershell
python scripts\generate_analysis_json.py `
  markush-run\benchmark\EP18885399\EP18885399-benchmark-input.json `
  -o markush-run\benchmark\EP18885399\EP18885399-analysis.json `
  --dry-run
```

拆分式 dry-run 会分别写出 `*.meta.prompt.txt`、`*.novelty.prompt.txt` 等子请求 prompt，方便检查每一步实际发送内容：

```powershell
python scripts\generate_analysis_json_split.py `
  markush-run\benchmark\EP21842292\EP21842292-benchmark-input.json `
  -o markush-run\benchmark\EP21842292\EP21842292-analysis.split-gpt55.json `
  --dry-run
```

将分析 JSON 转成 HTML 报告：

```powershell
python scripts\json_to_html_report.py `
  markush-run\benchmark\EP18885399\EP18885399-analysis.json `
  -o markush-run\benchmark\EP18885399\EP18885399-analysis.html
```

## Manifest 批量运行

当前 EP 审查文件候选清单位于：

```text
markush-run\benchmark\ep_review_file_sources_merged_current.json
```

推荐分两段跑。第一段只抓取和准备材料，不调用 GPT：

```powershell
python scripts\run_manifest_benchmark_batch.py `
  --manifest markush-run/benchmark/ep_review_file_sources_merged_current.json `
  --output-root markush-run/benchmark-api50 `
  --stage collect `
  --env-file .env `
  --api-key-env OPENAI_API_KEY `
  --base-url https://yunwu.ai/v1 `
  --model gpt-5.5 `
  --skip-existing `
  --workers 2
```

第二段在已有 benchmark input 上运行 refine、拆分式主分析、翻译和 HTML 渲染：

```powershell
python scripts\run_manifest_benchmark_batch.py `
  --manifest markush-run/benchmark/ep_review_file_sources_merged_current.json `
  --output-root markush-run/benchmark-api50 `
  --stage analysis `
  --analysis-mode split `
  --env-file .env `
  --api-key-env OPENAI_API_KEY `
  --base-url https://yunwu.ai/v1 `
  --model gpt-5.5 `
  --write-analysis-steps `
  --skip-existing `
  --workers 2
```

`collect` 写出 `batch-collect-status.csv`；`analysis` 写出 `batch-analysis-status.csv`。如果仍想单条记录内连续跑完整链路，可以使用 `--stage all`，它会先 collect，再 analysis。

### 靶点驱动 500 个原始资料集

如果要按现有靶点关键词扩展到 500 个可跑 case，使用端到端入口：

```powershell
python scripts\run_target_benchmark_raw_materials.py `
  --candidate-source manifest `
  --manifest markush-run\benchmark\ep_review_file_sources_target500.json `
  --target 500 `
  --output-root markush-run\benchmark-target500
```

这个入口默认复用已有 verified manifest，不重新做候选发现；后续完全走现有批处理链路：调用 `run_manifest_benchmark_batch.py --stage collect` 抓取审查文件和原始申请文件、调用 `download_prior_art_pdfs.py` 处理引用/相关专利 PDF、最后生成 `_raw_materials_audit.csv` 和 `_raw_materials_audit_summary.json`。

默认不使用 Google Patents。引用/相关专利 PDF 如果没有可直接使用的官方 PDF URL，会标记为 `official_pdf_source_unavailable`，不会伪造本地文件。只有显式加 `--allow-google-prior-art-fallback` 时，才会把 Google Patents/patentimages 作为 PDF fallback。

日常 target500 下载应优先复用已经验证过的 manifest；候选扩展和 doclist 验证独立完成，避免下载阶段重复做候选发现。

也可以分步运行：

```powershell
python scripts\build_target_review_manifest.py `
  --candidates markush-run\benchmark\ep_application_candidates_500.json `
  --output markush-run\benchmark\ep_review_file_sources_target500.json `
  --target 500 `
  --workers 2

python scripts\run_manifest_benchmark_batch.py `
  --manifest markush-run/benchmark/ep_review_file_sources_target500.json `
  --output-root markush-run/benchmark-target500 `
  --stage collect `
  --extract-pdf-text `
  --skip-existing `
  --success-target 500 `
  --workers 2

python scripts\download_prior_art_pdfs.py markush-run\benchmark-target500

python scripts\audit_raw_materials.py `
  markush-run\benchmark-target500 `
  --manifest markush-run\benchmark\ep_review_file_sources_target500.json
```

如果 manifest 正在后台构造，可以挂一个 watcher 等满 500 后自动启动下载：

```powershell
python scripts\wait_for_verified_manifest_and_collect.py `
  --manifest markush-run\benchmark\ep_review_file_sources_verified500_keywords.json `
  --output-root markush-run\benchmark-target500 `
  --target 500 `
  --interval-seconds 300 `
  --collect-workers 1
```

`--success-target` 只改变目标模式下的投递策略：达到指定成功数后停止继续投递新记录；不传该参数时，原有 manifest 批处理行为保持不变。

### EPO 下载稳定性

EPO Register/PDF 下载偶尔会返回 Cloudflare challenge 或连接 EOF。当前链路已做以下处理：

- main/doclist/PDF 请求带重试和线性退避；
- PDF 下载阶段单个文档失败不会直接中断整个 case；
- 每个 `docs\` 或 `original-application\` 目录会写出 `download-index.csv`；
- 失败文档会写入 `download-failures.csv`，后续可补抓。

如使用合规的固定出口代理，可在运行前设置：

```powershell
$env:EPO_PROXY_URL = "http://127.0.0.1:7890"
```

不建议使用代理池轮换 IP 去规避站点限流；更稳定的方式是低并发、重试、失败清单和断点续跑。

## Benchmark Input

`scripts/build_benchmark_input.py` 会生成顶层结构：

```json
{
  "application_number": "EP18885399",
  "benchmark_input": {},
  "source_trace": {}
}
```

核心字段包括：

| 字段 | 说明 |
| --- | --- |
| `drug_structure` | Markush、Formula、SMILES 等结构相关文本片段 |
| `claim_text` | 目标权利要求文本，默认优先抽取 claim 1 |
| `jurisdiction` | 法域，EPO 链路输出为 `EP` |
| `filing_date` | 申请日 |
| `priority_date` | 优先权日 |
| `specification_data` | 实施例、药效数据、合成路线、用途描述、对比数据 |
| `prior_art_docs` | Top-k 相关先文，默认 `k=10` |

更完整的 schema 见 `BENCHMARK_SCHEMA.md`。

## 主要文件

| 路径 | 说明 |
| --- | --- |
| `scripts/run_epo_benchmark.ps1` | 一键 pipeline 入口 |
| `scripts/fetch-epo-main.ps1` | 抓取 EPO Register main page |
| `scripts/fetch-epo-doclist.ps1` | 抓取 EPO Register doclist |
| `scripts/download-epo-docs.ps1` | 下载关键 EPO 文档 |
| `scripts/ocr-pdfs.py` | PDF OCR |
| `scripts/build_benchmark_input.py` | 构建 benchmark input JSON |
| `scripts/generate_analysis_json.py` | 调用 LLM 生成分析 JSON |
| `scripts/generate_analysis_json_split.py` | 将主分析拆成多次较小 LLM 调用并合并 JSON |
| `scripts/run_manifest_benchmark_batch.py` | 按 manifest 批量跑 EP benchmark |
| `scripts/run_case_set_refresh.py` | 刷新已有单 case 或嵌套 case set，串联 OCR、claims review、analysis、修复、验证和审计 |
| `scripts/generate_claims_verified.py` | 从本地 EPO claim PDF 生成 claims review HTML 和 claims verified JSON 草稿 |
| `scripts/repair_report_sources.py` | 用本地 TXT 连续原文修复 analysis JSON 中较弱的 `original_text` |
| `scripts/ensure_html_field_completeness.py` | 补齐 HTML 报告会渲染的关键字段 |
| `scripts/verify_report_sources.py` | 校验报告证据原文是否可追溯到本地 TXT |
| `scripts/audit_case_quality.py` | 输出 case 质量和证据可追溯性审计 |
| `scripts/validate_case_set_completeness.py` | 校验 case set 的 PDF/TXT/JSON/HTML/claims verified 完整性 |
| `scripts/json_to_html_report.py` | 渲染 HTML 报告 |

## Git

查看远程：

```powershell
git remote -v
```

推送到远程 `main`：

```powershell
git push origin main
```
