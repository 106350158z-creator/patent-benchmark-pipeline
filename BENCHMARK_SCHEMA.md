# Benchmark Schema

## Benchmark Input

`scripts/build_benchmark_input.py` 输出的 JSON 顶层结构：

```json
{
  "application_number": "EP18885399.8",
  "benchmark_input": {},
  "source_trace": {}
}
```

### `benchmark_input`

| 字段 | 类型 | 说明 |
|---|---|---|
| `drug_structure` | object | Markush、Formula、SMILES 相关结构信息 |
| `claim_text` | object | 目标权利要求原文，默认优先抽取 claim 1 |
| `jurisdiction` | string | 法域，目前 EPO 链路输出 `EP` |
| `filing_date` | string | 申请日，ISO 格式优先 |
| `priority_date` | string | 优先权日，ISO 格式优先 |
| `specification_data` | object | 说明书支持信息，包括实施例、药效数据、合成路线、用途描述、对比数据 |
| `prior_art_docs` | array | Top-k 相关先文，默认 k=10 |

### `drug_structure`

| 字段 | 类型 | 说明 |
|---|---|---|
| `markush_or_formula_snippets` | array | 从 claim/说明书中抽取的 Markush/Formula 邻近文本 |
| `smiles` | array | 自动识别到的 SMILES 字符串 |
| `extraction_note` | string | 结构抽取说明 |

### `claim_text`

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | string | claim 文本来源文件 |
| `claim_1` | string | claim 1 原文或最接近 claim 1 的文本 |
| `target_claims` | array | 目标权利要求列表 |

### `specification_data`

| 字段 | 类型 | 说明 |
|---|---|---|
| `examples` | array | 实施例片段 |
| `pharmacology_or_effect_data` | array | 药效、活性、毒性、MIC、viability 等数据片段 |
| `synthesis_routes` | array | 合成路线或制备方法片段 |
| `use_descriptions` | array | 用途、治疗对象、适应症片段 |
| `comparative_data` | array | 与现有技术或对照化合物的比较数据片段 |

### `prior_art_docs`

| 字段 | 类型 | 说明 |
|---|---|---|
| `rank` | number | 排名 |
| `citation` | string | 先文编号或文献名 |
| `mentions` | number | 在文本中的出现次数 |
| `sources` | array | 来源文件 |

## Benchmark Output

Benchmark output 是最终 HTML 报告，由审查分析 JSON 经以下脚本渲染：

```powershell
python scripts\json_to_html_report.py analysis.json -o analysis.html
```

HTML 报告展示：

- 基本信息；
- 综合评分；
- 新颖性、创造性、充分公开/支持、清楚性、单一性、适格性评分和理由；
- 主要风险；
- 建议动作；
- 证据链。

