# AGENTS.md

## Benchmark Overview Index

The canonical inventory is `markush-run/benchmark/benchmark-overview.json`. It identifies the current target benchmark EP applications and, for every discovered EP case directory, records every captured file path and type.

- Rebuild it with `python scripts/collection/build_benchmark_overview.py` after collection work. Do not hand-append JSON: the script performs an idempotent on-disk reconciliation, so reruns cannot duplicate files and deleted files are removed from the index.
- The standard single-case and manifest-batch entrypoints refresh the overview automatically when they finish, including after a partial collection failure.
- When adding a new collection entrypoint or a script that writes case artifacts, call the overview generator after it writes files. Keep the target manifest current; the default is the latest `ep_review_file_sources_full_*.json` manifest.

## EPO Benchmark Pipeline

处理本仓库的 EPO benchmark、Markush 审查文件、OCR、analysis JSON 或 HTML 报告问题前，先阅读 `workflow-summary.md`，再沿实际脚本链路排查。

优先入口：

- 单个 EP case：`scripts/run_epo_benchmark.ps1`
- 已有 case set 或嵌套目录批量刷新：`scripts/run_case_set_refresh.py`
- Manifest 批量任务：`scripts/run_manifest_benchmark_batch.py`

排查顺序：

1. 先看入口脚本的实际参数和默认值。
2. 再追 `scripts/build_benchmark_input.py`、`scripts/generate_analysis_json.py`、`scripts/generate_analysis_json_split.py`、`scripts/json_to_html_report.py`。
3. 涉及证据或空字段时，同时检查 `scripts/repair_report_sources.py`、`scripts/ensure_html_field_completeness.py`、`scripts/verify_report_sources.py`、`scripts/audit_case_quality.py` 和 `scripts/validate_case_set_completeness.py`。

重要约定：

- 默认使用拆分式分析 `AnalysisMode split`。
- 默认保留证据修复、HTML 字段补齐、证据验证、质量审计和完整性校验。
- `original_text` 必须能从本地 TXT 中追溯到连续原文片段。
- 不要只改 HTML 渲染层来掩盖 input、OCR 或 analysis JSON 的问题。
- 提交或推送时检查 `AGENTS.md`，不要遗漏本文件。
