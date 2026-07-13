from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from generate_analysis_json import AGGREGATE_WEIGHTS, normalize_analysis_result, read_json
from generate_analysis_json_split import DIMENSIONS, derive_source_sentence_lists, dimension_result_complete


DIMENSION_BY_NAME = {item["name"]: item for item in DIMENSIONS}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_analysis_shell(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("meta", {})
    data.setdefault("grant_label", "")
    data.setdefault("dimension_scores", {})
    data.setdefault("aggregate_score", 0)
    data.setdefault("top_risk_reasons", [])
    data.setdefault("recommended_actions", [])
    evidence = data.setdefault("evidence_trace", {})
    evidence.setdefault("prior_art_documents", [])
    evidence.setdefault("affected_claims", [])
    evidence.setdefault("specification_support", [])
    evidence.setdefault("examination_material_evidence", [])
    evidence.setdefault("examination_rounds", 1)
    return data


def partial_from_analysis(data: dict[str, Any], dimension: dict[str, str]) -> dict[str, Any]:
    scores = data.get("dimension_scores") or {}
    return {
        dimension["score_key"]: scores.get(dimension["score_key"]),
        dimension["disc_key"]: scores.get(dimension["disc_key"]),
    }


def incomplete_dimensions(data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for dimension in DIMENSIONS:
        if not dimension_result_complete(dimension, partial_from_analysis(data, dimension)):
            missing.append(dimension["name"])
    return missing


def append_dedup(items: list[Any], value: Any) -> None:
    key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    seen = {
        json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        for item in items
    }
    if key not in seen:
        items.append(value)


def merge_meta(target: dict[str, Any], source: dict[str, Any]) -> None:
    meta = target.setdefault("meta", {})
    source_meta = source.get("meta") or {}
    if isinstance(source_meta, dict):
        for key, value in source_meta.items():
            if value and not meta.get(key):
                meta[key] = value
    if source.get("grant_label") and not target.get("grant_label"):
        target["grant_label"] = source["grant_label"]
    evidence = target.setdefault("evidence_trace", {})
    if source.get("examination_rounds") and not evidence.get("examination_rounds"):
        evidence["examination_rounds"] = source["examination_rounds"]


def merge_dimension(target: dict[str, Any], dimension: dict[str, str], partial: dict[str, Any]) -> bool:
    if not dimension_result_complete(dimension, partial):
        return False

    scores = target.setdefault("dimension_scores", {})
    scores[dimension["score_key"]] = partial.get(dimension["score_key"])
    scores[dimension["disc_key"]] = partial.get(dimension["disc_key"])

    evidence = target.setdefault("evidence_trace", {})
    affected_claims = evidence.setdefault("affected_claims", [])
    for claim in partial.get("affected_claims") or []:
        try:
            claim_value: Any = int(claim)
        except (TypeError, ValueError):
            claim_value = claim
        append_dedup(affected_claims, claim_value)

    examination_evidence = evidence.setdefault("examination_material_evidence", [])
    for item in partial.get("evidence") or []:
        if isinstance(item, dict):
            append_dedup(examination_evidence, item)

    if dimension["name"] == "support":
        support_items = evidence.setdefault("specification_support", [])
        disc = partial.get(dimension["disc_key"])
        if isinstance(disc, dict) and disc.get("original_text"):
            append_dedup(
                support_items,
                {
                    "location": disc.get("source") or "support_disc",
                    "original_text": disc.get("original_text") or "",
                    "translation": disc.get("translation") or "",
                    "llm_evidence_explanation": disc.get("llm_evidence_explanation") or "",
                },
            )
        for item in partial.get("evidence") or []:
            if isinstance(item, dict) and item.get("original_text"):
                append_dedup(
                    support_items,
                    {
                        "location": item.get("source") or item.get("location") or "support evidence",
                        "original_text": item.get("original_text") or "",
                        "translation": item.get("translation") or "",
                        "llm_evidence_explanation": item.get("llm_evidence_explanation") or "",
                    },
                )

    risk = str(partial.get("risk_reason") or "").strip()
    if risk:
        append_dedup(target.setdefault("top_risk_reasons", []), risk)
    action = str(partial.get("recommended_action") or "").strip()
    if action:
        append_dedup(target.setdefault("recommended_actions", []), action)
    return True


def run_split_completion(args: argparse.Namespace, dimensions: list[str], pass_index: int) -> Path:
    output = Path(args.analysis_json)
    temp_output = output.with_name(f"{output.stem}.completion-pass{pass_index}.json")
    command = [
        sys.executable,
        "scripts/generate_analysis_json_split.py",
        args.benchmark_input,
        "-o",
        str(temp_output),
        "--env-file",
        args.env_file,
        "--api-key-env",
        args.api_key_env,
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--max-source-files",
        str(args.max_source_files),
        "--max-chars-per-file",
        str(args.max_chars_per_file),
        "--max-field-chars",
        str(args.max_field_chars),
        "--max-prior-art",
        str(args.max_prior_art),
        "--max-tokens",
        str(args.max_tokens),
        "--meta-max-tokens",
        str(args.meta_max_tokens),
        "--request-timeout",
        str(args.request_timeout),
        "--retries",
        str(args.retries),
        "--reasoning-effort",
        args.reasoning_effort,
        "--verbosity",
        args.verbosity,
        "--write-steps",
        "--skip-meta",
        "--only-dimensions",
        *dimensions,
    ]
    if args.allow_low_quality_source:
        command.append("--allow-low-quality-source")
    if args.omit_temperature:
        command.append("--omit-temperature")
    else:
        command.extend(["--temperature", str(args.temperature)])
    print("[complete]", " ".join(command), flush=True)
    subprocess.run(command, cwd=args.project_root, check=True)
    return temp_output


def merge_completion_pass(analysis: dict[str, Any], completion_output: Path, dimensions: list[str]) -> list[str]:
    repaired: list[str] = []
    if completion_output.exists():
        merge_meta(analysis, read_json(completion_output))
    for name in dimensions:
        dimension = DIMENSION_BY_NAME[name]
        step_path = completion_output.with_name(f"{completion_output.stem}.{name}.json")
        if not step_path.exists():
            continue
        partial = read_json(step_path)
        if merge_dimension(analysis, dimension, partial):
            repaired.append(name)
    return repaired


def recompute_aggregate(data: dict[str, Any]) -> None:
    scores = data.get("dimension_scores") or {}
    weighted = 0.0
    total_weight = 0.0
    for score_key, weight in AGGREGATE_WEIGHTS.items():
        try:
            score = float(scores.get(score_key))
        except (TypeError, ValueError):
            continue
        weighted += score * weight
        total_weight += weight
    if total_weight:
        data["aggregate_score"] = int(round(weighted / total_weight))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerun only incomplete split-analysis dimensions and merge them into an analysis JSON.")
    parser.add_argument("benchmark_input")
    parser.add_argument("analysis_json")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://yunwu.ai/v1")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--max-source-files", type=int, default=8)
    parser.add_argument("--max-chars-per-file", type=int, default=5000)
    parser.add_argument("--max-field-chars", type=int, default=5000)
    parser.add_argument("--max-prior-art", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=3000)
    parser.add_argument("--meta-max-tokens", type=int, default=1200)
    parser.add_argument("--request-timeout", type=float, default=240)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--verbosity", default="low")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--omit-temperature", action="store_true")
    parser.add_argument("--allow-low-quality-source", action="store_true")
    parser.add_argument("--fail-if-incomplete", action="store_true")
    parser.add_argument("--status-json", default="")
    args = parser.parse_args()

    args.project_root = str(Path(args.project_root).resolve())
    analysis_path = Path(args.analysis_json)
    benchmark = read_json(Path(args.benchmark_input))
    analysis = ensure_analysis_shell(read_json(analysis_path) if analysis_path.exists() else {})
    repaired_total: list[str] = []

    for pass_index in range(1, args.passes + 1):
        missing = incomplete_dimensions(analysis)
        if not missing:
            break
        completion_output = run_split_completion(args, missing, pass_index)
        repaired = merge_completion_pass(analysis, completion_output, missing)
        repaired_total.extend(repaired)
        analysis = normalize_analysis_result(analysis, benchmark)
        derive_source_sentence_lists(analysis)
        recompute_aggregate(analysis)
        write_json(analysis_path, analysis)
        print(f"[complete] pass={pass_index} missing={missing} repaired={repaired}", flush=True)

    final_missing = incomplete_dimensions(analysis)
    analysis["analysis_completion_status"] = {
        "repaired_dimensions": repaired_total,
        "missing_dimensions": final_missing,
        "complete": not final_missing,
    }
    if not final_missing:
        analysis.pop("automatic_placeholder_fields_added", None)
    write_json(analysis_path, analysis)

    status = {
        "analysis_json": str(analysis_path),
        "complete": not final_missing,
        "repaired_dimensions": repaired_total,
        "missing_dimensions": final_missing,
    }
    if args.status_json:
        write_json(Path(args.status_json), status)
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
    if final_missing and args.fail_if_incomplete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
