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
- 调用 LLM 生成专利审查分析 JSON
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
  -OutputRoot .\runs `
  -RunOcr
```

抓取材料、OCR、生成 benchmark input、调用 LLM，并渲染 HTML：

```powershell
.\scripts\run_epo_benchmark.ps1 `
  -ApplicationNumber EP18885399 `
  -OutputRoot .\runs `
  -RunOcr `
  -GenerateAnalysis
```

输出目录默认位于：

```text
runs\<ApplicationNumber>\
```

## 分步运行

如果目录中已经有 `*-main.html`、`*-doclist.csv` 和 `docs/*.txt`，可以直接生成 benchmark input：

```powershell
python scripts\build_benchmark_input.py `
  markush-run\EP18885399_granted `
  --application-number EP18885399 `
  -o markush-run\EP18885399_granted\EP18885399-benchmark-input.json
```

生成分析 JSON：

```powershell
python scripts\generate_analysis_json.py `
  markush-run\EP18885399_granted\EP18885399-benchmark-input.json `
  -o markush-run\EP18885399_granted\EP18885399-analysis.json
```

只生成 prompt，不调用 API：

```powershell
python scripts\generate_analysis_json.py `
  markush-run\EP18885399_granted\EP18885399-benchmark-input.json `
  -o markush-run\EP18885399_granted\EP18885399-analysis.json `
  --dry-run
```

将分析 JSON 转成 HTML 报告：

```powershell
python scripts\json_to_html_report.py `
  markush-run\EP18885399_granted\EP18885399-analysis.json `
  -o markush-run\EP18885399_granted\EP18885399-analysis.html
```

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
