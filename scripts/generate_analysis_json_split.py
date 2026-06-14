import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from generate_analysis_json import (
    AGGREGATE_WEIGHTS,
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    collect_source_texts,
    extract_json_object,
    load_dotenv,
    normalize_analysis_result,
    read_json,
)


DIMENSIONS = [
    {
        "name": "novelty",
        "label": "新颖性",
        "score_key": "novelty_score",
        "disc_key": "novelty_disc",
        "issue": "novelty",
        "focus": "EP novelty / Art.54. 判断是否被单一现有技术直接、明确公开。",
    },
    {
        "name": "inventive_step",
        "label": "创造性",
        "score_key": "inventive_step_score",
        "disc_key": "inventive_step_disc",
        "issue": "inventive_step",
        "focus": "EP inventive step / Art.56. 优先按 problem-solution approach；如涉及非技术特征，说明 COMVIK 逻辑。",
    },
    {
        "name": "support",
        "label": "充分公开/支持",
        "score_key": "support_score",
        "disc_key": "support_disc",
        "issue": "support",
        "focus": "EP sufficiency/support / Art.83, Art.84, Art.123(2). 判断说明书实施、原始公开和权利要求支持。",
    },
    {
        "name": "clarity",
        "label": "清楚性",
        "score_key": "clarity_score",
        "disc_key": "clarity_disc",
        "issue": "clarity",
        "focus": "EP clarity / Art.84. 判断术语、边界、简洁性、必要技术特征和权利要求清楚性。",
    },
    {
        "name": "eligibility",
        "label": "适格性",
        "score_key": "eligibility_score",
        "disc_key": "eligibility_disc",
        "issue": "eligibility",
        "focus": "EP Art.52 patent eligibility. 判断是否属于排除主题，是否具有技术性或进一步技术效果。",
    },
]


SYSTEM_PROMPT = """你是专利审查报告分析专家。你必须只基于给定 benchmark input 和 SOURCE 审查材料输出合法 JSON。
禁止编造审查事实、链接、先文或原文证据。original_text 必须从一个连续 SOURCE 片段中逐字摘录，不能改写、拼接、补词或使用省略号。
只输出 JSON，不要输出 Markdown。"""


def truncate_value(value: Any, max_chars: int) -> Any:
    if max_chars <= 0:
        return value
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "\n[TRUNCATED]"
    if isinstance(value, list):
        return [truncate_value(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {key: truncate_value(item, max_chars) for key, item in value.items()}
    return value


def truncate_blob(value: Any, max_chars: int) -> Any:
    if max_chars <= 0:
        return value
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "\n[TRUNCATED]"
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text if len(text) <= max_chars else text[:max_chars] + "\n[TRUNCATED]"


def compact_benchmark(benchmark: dict[str, Any], max_prior_art: int, max_field_chars: int) -> dict[str, Any]:
    data = benchmark.get("benchmark_input") or {}
    keep_keys = [
        "jurisdiction",
        "application_number",
        "publication_number",
        "title",
        "applicant",
        "filing_date",
        "priority_date",
        "claim_text",
        "drug_structure",
        "specification_data",
        "known_outcome",
        "prior_art_docs",
    ]
    compact = {
        "application_number": benchmark.get("application_number") or data.get("application_number"),
        "benchmark_input": {key: data.get(key) for key in keep_keys if key in data},
    }
    for key in ["claim_text", "drug_structure", "specification_data"]:
        if key in compact["benchmark_input"]:
            compact["benchmark_input"][key] = truncate_blob(compact["benchmark_input"][key], max_field_chars)
    prior_art = compact["benchmark_input"].get("prior_art_docs")
    if isinstance(prior_art, list):
        compact_prior_art = []
        for item in prior_art[:max_prior_art]:
            if not isinstance(item, dict):
                continue
            compact_prior_art.append(
                {
                    "rank": item.get("rank"),
                    "citation": item.get("citation"),
                    "mentioned_in_examined_text": item.get("mentioned_in_examined_text"),
                    "official_link": item.get("official_link"),
                    "official_source": item.get("official_source"),
                    "retrieval_method": item.get("retrieval_method"),
                }
            )
        compact["benchmark_input"]["prior_art_docs"] = compact_prior_art
    return compact


def source_block(source_texts: list[dict[str, str]]) -> str:
    return "\n\n".join(f"### SOURCE: {doc['name']}\n{doc['text']}" for doc in source_texts)


def call_json(
    prompt: str,
    *,
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int,
    request_timeout: float,
    token_limit_param: str,
    omit_temperature: bool,
    temperature: float,
    reasoning_effort: str | None,
    verbosity: str | None,
) -> dict[str, Any]:
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=request_timeout)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        token_limit_param: max_tokens,
        "response_format": {"type": "json_object"},
    }
    if not omit_temperature:
        kwargs["temperature"] = temperature
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if verbosity:
        kwargs["verbosity"] = verbosity
    response = client.chat.completions.create(**kwargs)
    return extract_json_object(response.choices[0].message.content or "{}")


def build_meta_prompt(benchmark: dict[str, Any], sources: str) -> str:
    return f"""从 benchmark input 和 SOURCE 中提取案件元数据和总体结论。

输出 JSON schema:
{{
  "meta": {{
    "jurisdiction": "CN|EP|US",
    "application_number": "申请号",
    "title": "发明名称",
    "applicant": "申请人",
    "filing_date": "申请日",
    "examination_date": "审查意见发文日或最终决定日",
    "outcome": "granted|rejected|withdrawn|pending"
  }},
  "grant_label": "yes|no",
  "examination_rounds": 1
}}

规则:
- grant_label 只根据审查/登记材料判断，授权为 yes，驳回/撤回/未授权为 no。
- 如果字段缺失，用空字符串，不要编造。
- 只输出上述 JSON。

benchmark input:
{json.dumps(benchmark, ensure_ascii=False, indent=2)}

SOURCE:
{sources}
"""


def build_dimension_prompt(benchmark: dict[str, Any], sources: str, dimension: dict[str, str]) -> str:
    score_key = dimension["score_key"]
    disc_key = dimension["disc_key"]
    issue = dimension["issue"]
    return f"""只分析一个授权性维度: {dimension['label']}。

关注点:
{dimension['focus']}

输出 JSON schema:
{{
  "{score_key}": 0,
  "{disc_key}": {{
    "analysis": "中文分析结论，说明审查意见、申请人修改/争辩和当前风险",
    "original_text": "从 SOURCE 连续摘录的英文/原文证据",
    "translation": "original_text 的中文翻译",
    "llm_evidence_explanation": "说明该原文如何支持本维度评分"
  }},
  "affected_claims": [1],
  "evidence": [
    {{
      "issue": "{issue}",
      "source": "文件名或段落",
      "original_text": "从 SOURCE 连续摘录的原文",
      "translation": "中文翻译",
      "llm_evidence_explanation": "证据说明"
    }}
  ],
  "risk_reason": "English risk sentence, <=25 words",
  "recommended_action": "Specific actionable English recommendation"
}}

评分标准:
- 100: 该维度完全通过审查，无任何异议
- 80: 有小问题但已通过修改克服
- 50: 存在争议，结果不确定
- 10-20: 被明确否定，审查员给出了充分否定理由
- 0: 完全不满足，无救济可能

规则:
- original_text 必须来自一个连续 SOURCE 片段，不能拼接、改写或使用省略号。
- 如果 SOURCE 未显示该维度被质疑，给高分，并引用能说明未见该类异议或案件结论的原文。
- 只输出上述 JSON。

benchmark input:
{json.dumps(benchmark, ensure_ascii=False, indent=2)}

SOURCE:
{sources}
"""


def merge_results(
    benchmark: dict[str, Any],
    meta_result: dict[str, Any],
    dimension_results: list[tuple[dict[str, str], dict[str, Any]]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "meta": meta_result.get("meta") or {},
        "grant_label": meta_result.get("grant_label") or "",
        "dimension_scores": {},
        "aggregate_score": 0,
        "top_risk_reasons": [],
        "recommended_actions": [],
        "evidence_trace": {
            "prior_art_documents": [],
            "affected_claims": [],
            "specification_support": [],
            "examination_material_evidence": [],
            "examination_rounds": meta_result.get("examination_rounds") or 1,
        },
    }

    affected_claims: set[int] = set()
    for dimension, partial in dimension_results:
        score_key = dimension["score_key"]
        disc_key = dimension["disc_key"]
        result["dimension_scores"][score_key] = partial.get(score_key)
        result["dimension_scores"][disc_key] = partial.get(disc_key) or {
            "analysis": "",
            "original_text": "",
            "translation": "",
            "llm_evidence_explanation": "",
        }

        for claim in partial.get("affected_claims") or []:
            try:
                affected_claims.add(int(claim))
            except (TypeError, ValueError):
                continue

        risk = str(partial.get("risk_reason") or "").strip()
        if risk:
            result["top_risk_reasons"].append(risk)
        action = str(partial.get("recommended_action") or "").strip()
        if action:
            result["recommended_actions"].append(action)

        evidence_items = partial.get("evidence") or []
        if isinstance(evidence_items, list):
            for item in evidence_items:
                if isinstance(item, dict):
                    result["evidence_trace"]["examination_material_evidence"].append(item)

    result["evidence_trace"]["affected_claims"] = sorted(affected_claims)
    result["top_risk_reasons"] = result["top_risk_reasons"][:5]
    result["recommended_actions"] = result["recommended_actions"][:5]
    return normalize_analysis_result(result, benchmark)


def write_step(path: Path, name: str, data: dict[str, Any]) -> None:
    step_path = path.with_name(f"{path.stem}.{name}.json")
    step_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate patent analysis JSON with smaller split API calls.")
    parser.add_argument("benchmark_input", help="Path to benchmark input JSON.")
    parser.add_argument("-o", "--output", required=True, help="Output analysis JSON path.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.environ.get("OHMYGPT_API_BASE") or os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--meta-max-tokens", type=int, default=700)
    parser.add_argument("--token-limit-param", choices=["max_tokens", "max_completion_tokens"], default="max_completion_tokens")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--omit-temperature", action="store_true", default=False)
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--reasoning-effort", default=os.environ.get("OPENAI_REASONING_EFFORT") or "low")
    parser.add_argument("--verbosity", default=os.environ.get("OPENAI_VERBOSITY") or "low")
    parser.add_argument("--max-chars-per-file", type=int, default=3000)
    parser.add_argument("--max-source-files", type=int, default=3)
    parser.add_argument("--max-prior-art", type=int, default=12)
    parser.add_argument("--max-field-chars", type=int, default=3000)
    parser.add_argument("--only-dimensions", nargs="*", choices=[item["name"] for item in DIMENSIONS])
    parser.add_argument("--write-steps", action="store_true", help="Write one JSON file for each API sub-call.")
    parser.add_argument("--dry-run", action="store_true", help="Write split prompts next to output, but do not call the API.")
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key and not args.dry_run:
        raise RuntimeError(f"API key not found. Set {args.api_key_env} in {args.env_file} or the environment.")

    benchmark = read_json(Path(args.benchmark_input))
    compact = compact_benchmark(benchmark, args.max_prior_art, args.max_field_chars)
    sources = source_block(collect_source_texts(benchmark, args.max_chars_per_file, args.max_source_files))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    selected_dimensions = [
        item for item in DIMENSIONS if not args.only_dimensions or item["name"] in args.only_dimensions
    ]
    prompts = {"meta": build_meta_prompt(compact, sources)}
    prompts.update({item["name"]: build_dimension_prompt(compact, sources, item) for item in selected_dimensions})

    if args.dry_run:
        for name, prompt in prompts.items():
            prompt_path = output_path.with_name(f"{output_path.stem}.{name}.prompt.txt")
            prompt_path.write_text(prompt, encoding="utf-8")
            print(f"Wrote prompt: {prompt_path} ({len(prompt)} chars)")
        return

    common = {
        "model": args.model,
        "base_url": args.base_url,
        "api_key": api_key,
        "request_timeout": args.request_timeout,
        "token_limit_param": args.token_limit_param,
        "omit_temperature": args.omit_temperature,
        "temperature": args.temperature,
        "reasoning_effort": args.reasoning_effort,
        "verbosity": args.verbosity,
    }

    started = time.monotonic()
    meta_result = call_json(prompts["meta"], max_tokens=args.meta_max_tokens, **common)
    print(f"meta call completed in {time.monotonic() - started:.1f}s")
    if args.write_steps:
        write_step(output_path, "meta", meta_result)

    dimension_results: list[tuple[dict[str, str], dict[str, Any]]] = []
    for dimension in selected_dimensions:
        step_started = time.monotonic()
        partial = call_json(prompts[dimension["name"]], max_tokens=args.max_tokens, **common)
        print(f"{dimension['name']} call completed in {time.monotonic() - step_started:.1f}s")
        if args.write_steps:
            write_step(output_path, dimension["name"], partial)
        dimension_results.append((dimension, partial))

    result = merge_results(benchmark, meta_result, dimension_results)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote split analysis JSON: {output_path} ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    main()
