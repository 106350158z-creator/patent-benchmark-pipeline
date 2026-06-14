# EPO Patent Benchmark Pipeline

这个仓库用于从 EPO Register 抓取欧洲专利申请材料，整理 benchmark input，调用 OpenAI-compatible LLM 生成审查分析 JSON，并渲染为 HTML 报告。

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
  -GenerateAnalysis
```

如果使用 `gpt-5.5` 且 OpenAI-compatible 网关对长请求不稳定，推荐使用拆分式主分析。该模式会把一次完整审查分析拆成 `meta`、`novelty`、`inventive_step`、`support`、`clarity`、`eligibility` 六次较小调用，再本地合并为同一结构的 analysis JSON：

```powershell
.\scripts\run_epo_benchmark.ps1 `
  -ApplicationNumber EP21842292 `
  -OutputRoot markush-run\benchmark-api50 `
  -RunOcr `
  -GenerateAnalysis `
  -AnalysisMode split `
  -EnvFile .env `
  -ApiKeyEnv OPENAI_API_KEY `
  -BaseUrl https://yunwu.ai/v1 `
  -Model gpt-5.5 `
  -MaxSourceFiles 3 `
  -MaxCharsPerFile 1800 `
  -MaxFieldChars 1800 `
  -MaxPriorArt 8 `
  -MaxTokens 1000 `
  -MetaMaxTokens 600 `
  -RequestTimeout 180 `
  -ReasoningEffort low `
  -Verbosity low `
  -WriteAnalysisSteps
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
