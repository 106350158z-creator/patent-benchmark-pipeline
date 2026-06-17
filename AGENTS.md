# AGENTS.md

## EPO Benchmark 链路

处理本仓库的 EPO benchmark、Markush 审查文件、OCR、analysis JSON 或 HTML 报告问题前，先阅读 [文档/链路脚本清单.md](文档/链路脚本清单.md)，再沿实际脚本链路排查。

优先从 `scripts/run_epo_benchmark.ps1` 和 `scripts/run_manifest_benchmark_batch.py` 判断入口，再追到 `build_benchmark_input.py`、`generate_analysis_json*.py` 和 `json_to_html_report.py`，不要只改渲染层来掩盖 input 或 OCR 的问题。

