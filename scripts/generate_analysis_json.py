import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_BASE_URL = "https://api.ohmygpt.com/v1"
DEFAULT_API_KEY_ENV = "OHMYGPT_API_KEY"


SYSTEM_PROMPT = """你是专利审查报告分析专家。你必须基于给定 benchmark input 和审查材料生成合法 JSON。
要求：
1. 只输出 JSON，不要输出 Markdown 或额外解释。
2. 对 novelty_disc / inventive_step_disc 等理由字段，必须输出结构化对象，包含 analysis、original_text、translation、llm_evidence_explanation。
3. 如果某维度未被质疑，给 100 分，disc 写“未被质疑”，并说明材料中未见该类异议。
4. EP 创造性分析优先使用 EPO problem-solution approach；如存在非技术特征，再说明 COMVIK 逻辑。
5. top_risk_reasons 用中文，每条不超过 50 字。
6. recommended_actions 必须具体可执行。
7. 不输出单一性 unity 相关评分字段。
8. aggregate_score 必须按权重计算并四舍五入：新颖性25%，创造性30%，充分公开/支持15%，清楚性10%，适格性20%。
9. evidence_trace 中证据必须保留审查材料原文的原始语言，同时给出中文翻译，并增加 LLM 证据说明。
"""


OUTPUT_SCHEMA = {
    "meta": {
        "jurisdiction": "CN|EP|US",
        "application_number": "申请号",
        "title": "发明名称",
        "applicant": "申请人",
        "filing_date": "申请日",
        "examination_date": "审查意见发文日或最终决定日",
        "outcome": "granted|rejected|pending",
    },
    "grant_label": "yes|no",
    "dimension_scores": {
        "novelty_score": "0-100",
        "novelty_disc": {
            "analysis": "中文分析结论，引用对比文件和区别特征",
            "original_text": "审查材料中对应新颖性的英文原文；如果材料未质疑新颖性，引用能说明未以Art.54拒绝的英文原文或最终结果英文原文",
            "translation": "上述英文原文的中文翻译",
            "llm_evidence_explanation": "LLM说明该原文如何支持本维度评分",
        },
        "inventive_step_score": "0-100",
        "inventive_step_disc": {
            "analysis": "中文分析结论，包括最接近现有技术、区别特征、技术效果",
            "original_text": "审查材料中对应创造性的英文原文",
            "translation": "上述英文原文的中文翻译",
            "llm_evidence_explanation": "LLM说明该原文如何支持本维度评分",
        },
        "support_score": "0-100",
        "support_disc": {
            "analysis": "中文分析结论",
            "original_text": "审查材料中对应充分公开/支持的英文原文；如未质疑，引用授权文本/说明书基础相关英文原文",
            "translation": "上述英文原文的中文翻译",
            "llm_evidence_explanation": "LLM说明该原文如何支持本维度评分",
        },
        "clarity_score": "0-100",
        "clarity_disc": {
            "analysis": "中文分析结论",
            "original_text": "审查材料中对应清楚性的英文原文",
            "translation": "上述英文原文的中文翻译",
            "llm_evidence_explanation": "LLM说明该原文如何支持本维度评分",
        },
        "eligibility_score": "0-100",
        "eligibility_disc": {
            "analysis": "中文分析结论",
            "original_text": "审查材料中对应EP Art.52适格性的英文原文",
            "translation": "上述英文原文的中文翻译",
            "llm_evidence_explanation": "LLM说明该原文如何支持本维度评分",
        },
    },
    "aggregate_score": "0-100",
    "top_risk_reasons": ["中文风险短句"],
    "recommended_actions": ["具体可执行建议"],
    "evidence_trace": {
        "prior_art_documents": [
            {
                "rank": 1,
                "citation": "对比文件/语义检索相关专利",
                "mentioned_in_examined_text": True,
                "official_link": "EPO/Espacenet 官方链接",
                "llm_evidence_explanation": "该先文与审查意见或权利要求的关系",
            }
        ],
        "affected_claims": [1, 2, 3],
        "specification_support": [
            {
                "location": "页码/段落/文件名",
                "original_text": "审查材料原文，保持原语言",
                "translation": "中文翻译；如原文为中文，则给英文翻译",
                "llm_evidence_explanation": "LLM 对该证据如何支撑结论的说明",
            }
        ],
        "examination_material_evidence": [
            {
                "issue": "novelty|inventive_step|support|clarity|eligibility|outcome",
                "source": "文件名/页码/段落",
                "original_text": "审查材料原文，保持原语言",
                "translation": "中文翻译；如原文为中文，则给英文翻译",
                "llm_evidence_explanation": "LLM 证据说明",
            }
        ],
        "examination_rounds": 1,
    },
}


AGGREGATE_WEIGHTS = {
    "novelty_score": 0.25,
    "inventive_step_score": 0.30,
    "support_score": 0.15,
    "clarity_score": 0.10,
    "eligibility_score": 0.20,
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return ""
    return text[:max_chars] if max_chars > 0 else text


def collect_source_texts(benchmark: dict[str, Any], max_chars_per_file: int, max_files: int) -> list[dict[str, str]]:
    trace = benchmark.get("source_trace") or {}
    docs_dir = Path(str(trace.get("docs_dir") or ""))
    used_names = trace.get("text_documents_used") or []
    docs: list[dict[str, str]] = []

    if docs_dir.exists():
        for name in used_names[:max_files]:
            path = docs_dir / name
            text = read_text(path, max_chars_per_file)
            if text.strip():
                docs.append({"name": name, "text": text})

    main_html = Path(str(trace.get("main_html") or ""))
    if main_html.exists():
        docs.insert(0, {"name": main_html.name, "text": read_text(main_html, min(max_chars_per_file, 12000))})

    return docs[:max_files]


def build_user_prompt(benchmark: dict[str, Any], source_texts: list[dict[str, str]]) -> str:
    compact_benchmark = json.dumps(benchmark, ensure_ascii=False, indent=2)
    source_block = "\n\n".join(
        f"### SOURCE: {doc['name']}\n{doc['text']}" for doc in source_texts
    )
    schema = json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    return f"""请根据以下 benchmark input 和审查材料生成最终专利审查分析 JSON。

输出 JSON Schema：
{schema}

评分标准：
- 100分：该维度完全通过审查，无任何异议
- 80分：有小问题但已通过修改克服
- 50分：存在争议，结果不确定
- 10-20分：被明确否定，审查员给出了充分否定理由
- 0分：完全不满足，无救济可能

输出要求：
- 只保留新颖性、创造性、充分公开/支持、清楚性、适格性五个评分维度；不要输出单一性。
- aggregate_score 按新权重计算：新颖性25%，创造性30%，充分公开/支持15%，清楚性10%，适格性20%。
- evidence_trace.prior_art_documents 必须优先使用 benchmark_input.prior_art_docs，保留 top20；其中 mentioned_in_examined_text=true 的为审查文本已提及先文，其余为 EPO/Espacenet 官网语义检索补充结果。
- dimension_scores 中每个 *_disc 都必须是对象，而不是字符串；每个对象必须包含 analysis、original_text、translation、llm_evidence_explanation。
- 每个维度的 original_text 必须使用审查材料里的英文原始描述文本；不要只写中文概括。
- evidence_trace 中的 specification_support 和 examination_material_evidence 必须包含 original_text、translation、llm_evidence_explanation。
- original_text 必须保留审查材料原始语言和原始描述，不要先改写；translation 放在旁边。

benchmark input：
{compact_benchmark}

审查材料文本：
{source_block}
"""


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.S | re.I)
    if fence:
        stripped = fence.group(1)
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    return json.loads(stripped)


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_analysis_result(result: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    scores = result.setdefault("dimension_scores", {})
    scores.pop("unity_score", None)
    scores.pop("unity_disc", None)
    for disc_key in [
        "novelty_disc",
        "inventive_step_disc",
        "support_disc",
        "clarity_disc",
        "eligibility_disc",
    ]:
        value = scores.get(disc_key)
        if isinstance(value, str):
            scores[disc_key] = {
                "analysis": value,
                "original_text": "",
                "translation": "",
                "llm_evidence_explanation": "模型未返回该维度的结构化原文证据，已保留分析结论；请根据审查材料补齐原文和翻译。",
            }

    weighted = 0.0
    complete = True
    for key, weight in AGGREGATE_WEIGHTS.items():
        score = to_float(scores.get(key))
        if score is None:
            complete = False
            break
        weighted += score * weight
    if complete:
        result["aggregate_score"] = int(round(weighted))

    evidence = result.setdefault("evidence_trace", {})
    benchmark_prior_art = ((benchmark.get("benchmark_input") or {}).get("prior_art_docs") or [])[:20]
    existing = evidence.get("prior_art_documents") or []
    explanation_by_citation: dict[str, str] = {}
    for item in existing:
        if isinstance(item, dict):
            citation = str(item.get("citation") or "").strip().lower()
            explanation = str(item.get("llm_evidence_explanation") or item.get("relevance") or "").strip()
            if citation and explanation:
                explanation_by_citation[citation] = explanation

    if benchmark_prior_art:
        normalized_prior_art = []
        for index, item in enumerate(benchmark_prior_art, start=1):
            if not isinstance(item, dict):
                continue
            citation = str(item.get("citation") or "").strip()
            explanation = explanation_by_citation.get(citation.lower())
            if not explanation:
                if item.get("mentioned_in_examined_text"):
                    explanation = "该引用由审查文本直接提及，作为审查意见或检索意见中的对比文件线索。"
                else:
                    explanation = "该引用由 EPO/Espacenet 官网语义检索补充，用于扩展与权利要求主题相关的专利背景。"
            normalized_prior_art.append(
                {
                    "rank": item.get("rank") or index,
                    "citation": citation,
                    "mentioned_in_examined_text": bool(item.get("mentioned_in_examined_text")),
                    "retrieval_method": item.get("retrieval_method", ""),
                    "official_source": item.get("official_source", ""),
                    "official_link": item.get("official_link", ""),
                    "llm_evidence_explanation": explanation,
                }
            )
        evidence["prior_art_documents"] = normalized_prior_art[:20]

    return result


def call_model(
    prompt: str,
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
) -> str:
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
    return response.choices[0].message.content or ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate patent analysis JSON from benchmark input via an OpenAI-compatible API.")
    parser.add_argument("benchmark_input", help="Path to benchmark input JSON.")
    parser.add_argument("-o", "--output", required=True, help="Output analysis JSON path.")
    parser.add_argument("--env-file", default=".env", help="Dotenv file path. Defaults to .env in the current working directory.")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.environ.get("OHMYGPT_API_BASE") or os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--token-limit-param", choices=["max_tokens", "max_completion_tokens"], default="max_completion_tokens")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--omit-temperature", action="store_true", default=True)
    parser.add_argument("--request-timeout", type=float, default=360.0)
    parser.add_argument("--reasoning-effort", default=os.environ.get("OPENAI_REASONING_EFFORT") or "medium")
    parser.add_argument("--verbosity", default=os.environ.get("OPENAI_VERBOSITY") or "medium")
    parser.add_argument("--max-chars-per-file", type=int, default=18000)
    parser.add_argument("--max-source-files", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true", help="Build prompt and write it next to output, but do not call the API.")
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key and not args.dry_run:
        raise RuntimeError(f"API key not found. Set {args.api_key_env} in {args.env_file} or the environment.")

    benchmark_path = Path(args.benchmark_input)
    benchmark = read_json(benchmark_path)
    source_texts = collect_source_texts(benchmark, args.max_chars_per_file, args.max_source_files)
    prompt = build_user_prompt(benchmark, source_texts)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        prompt_path = output_path.with_suffix(".prompt.txt")
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"Wrote prompt: {prompt_path}")
        return

    raw = call_model(
        prompt=prompt,
        model=args.model,
        base_url=args.base_url,
        api_key=api_key,
        max_tokens=args.max_tokens,
        request_timeout=args.request_timeout,
        token_limit_param=args.token_limit_param,
        omit_temperature=args.omit_temperature,
        temperature=args.temperature,
        reasoning_effort=args.reasoning_effort,
        verbosity=args.verbosity,
    )
    result = normalize_analysis_result(extract_json_object(raw), benchmark)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote analysis JSON: {output_path}")


if __name__ == "__main__":
    main()
