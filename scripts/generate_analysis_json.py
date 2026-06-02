import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


DEFAULT_MODEL = "gpt5.5"
DEFAULT_BASE_URL = "https://api.ohmygpt.com/v1"
DEFAULT_API_KEY_ENV = "OHMYGPT_API_KEY"


SYSTEM_PROMPT = """你是专利审查报告分析专家。你必须基于给定 benchmark input 和审查材料生成合法 JSON。
要求：
1. 只输出 JSON，不要输出 Markdown 或额外解释。
2. 对 novelty_disc / inventive_step_disc 等理由字段，必须引用具体对比文件编号、区别特征和审查结论。
3. 如果某维度未被质疑，给 100 分，disc 写“未被质疑”，并说明材料中未见该类异议。
4. EP 创造性分析优先使用 EPO problem-solution approach；如存在非技术特征，再说明 COMVIK 逻辑。
5. top_risk_reasons 用中文，每条不超过 50 字。
6. recommended_actions 必须具体可执行。
7. aggregate_score 必须按权重计算并四舍五入：新颖性25%，创造性30%，充分公开/支持15%，清楚性10%，单一性5%，适格性15%。
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
        "novelty_disc": "新颖性评价的具体理由，引用对比文件和区别特征",
        "inventive_step_score": "0-100",
        "inventive_step_disc": "创造性评价，包括最接近现有技术、区别特征、技术效果",
        "support_score": "0-100",
        "support_disc": "充分公开/支持评价",
        "clarity_score": "0-100",
        "clarity_disc": "清楚性评价",
        "unity_score": "0-100",
        "unity_disc": "单一性评价",
        "eligibility_score": "0-100",
        "eligibility_disc": "适格性评价",
    },
    "aggregate_score": "0-100",
    "top_risk_reasons": ["中文风险短句"],
    "recommended_actions": ["具体可执行建议"],
    "evidence_trace": {
        "prior_art_documents": ["对比文件列表"],
        "affected_claims": [1, 2, 3],
        "specification_support": [{"location": "页码/段落/文件名", "relevance": "支撑说明"}],
        "examination_rounds": 1,
    },
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
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL)
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
    result = extract_json_object(raw)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote analysis JSON: {output_path}")


if __name__ == "__main__":
    main()

