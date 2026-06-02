# EPO Patent Benchmark Pipeline

这个项目用于给定欧洲专利申请号，抓取 EPO Register 文档，整理 benchmark 输入，并将最终分析 JSON 转成 HTML 报告。

## 输入输出

### Benchmark 输入字段

`scripts/build_benchmark_input.py` 会生成：

| 字段 | 含义 |
|---|---|
| `drug_structure` | Markush / Formula / SMILES 相关文本片段 |
| `claim_text` | 目标权利要求原文，默认优先抽取 claim 1 |
| `jurisdiction` | 法域，目前 EPO 链路输出 `EP` |
| `filing_date` | 申请日 |
| `priority_date` | 优先权日 |
| `specification_data` | 实施例、药效数据、合成路线、用途描述、对比数据 |
| `prior_art_docs` | Top-k 相关先文，默认 k=10 |

### Benchmark 输出

输出是 HTML 报告，由最终审查分析 JSON 转换得到：

```powershell
python scripts\json_to_html_report.py analysis.json -o analysis.html
```

## 一键抓取 benchmark 输入

```powershell
.\scripts\run_epo_benchmark.ps1 -ApplicationNumber EP18885399 -OutputRoot .\runs -RunOcr
```

这会执行：

1. 抓取 EPO Register main page；
2. 抓取 EPO Register doclist；
3. 下载审查意见、检索报告、答复、修改权利要求、授权文本等关键 PDF；
4. 可选 OCR；
5. 生成 benchmark input JSON。

## 已有样本生成 benchmark input

如果目录中已经有 `*-main.html`、`*-doclist.csv` 和 `docs/*.txt`：

```powershell
python scripts\build_benchmark_input.py markush-run\EP18885399_granted --application-number EP18885399 -o markush-run\EP18885399_granted\EP18885399-benchmark-input.json
```

## 正样本 HTML 输出

```powershell
python scripts\json_to_html_report.py markush-run\EP3720438_granted_analysis.json -o markush-run\EP3720438_granted_analysis.html
```

## LLM 生成最终分析 JSON

本项目使用 OpenAI-compatible Chat Completions。默认参数参考 `C:\Users\de'l'l\Markush\web\static\app.js`：

- model: `gpt5.5`
- base_url: `https://api.ohmygpt.com/v1`
- api_key_env: `OHMYGPT_API_KEY`

先复制 `.env.example` 为 `.env`，填入密钥：

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env` 已加入 `.gitignore`，不会进入 git。

生成 analysis JSON：

```powershell
python scripts\generate_analysis_json.py `
  markush-run\EP18885399_granted\EP18885399-benchmark-input.json `
  -o markush-run\EP18885399_granted\EP18885399-analysis.json
```

只生成 prompt、不调用 API：

```powershell
python scripts\generate_analysis_json.py `
  markush-run\EP18885399_granted\EP18885399-benchmark-input.json `
  -o markush-run\EP18885399_granted\EP18885399-analysis.json `
  --dry-run
```

一键抓取、OCR、生成 benchmark input、调用 LLM、渲染 HTML：

```powershell
.\scripts\run_epo_benchmark.ps1 `
  -ApplicationNumber EP18885399 `
  -OutputRoot .\runs `
  -RunOcr `
  -GenerateAnalysis
```

## LLM 位置

本项目负责抓取、OCR、benchmark 输入构造、LLM 调用和 HTML 渲染。LLM 调用脚本是 `scripts/generate_analysis_json.py`。
