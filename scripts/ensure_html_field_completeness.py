from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DIMENSION_KEYS = [
    ("novelty_score", "novelty_disc"),
    ("inventive_step_score", "inventive_step_disc"),
    ("support_score", "support_disc"),
    ("clarity_score", "clarity_disc"),
    ("eligibility_score", "eligibility_disc"),
]

DIMENSION_SOURCE_KEYS = [
    ("novelty", "novelty_score", "novelty_disc"),
    ("inventive_step", "inventive_step_score", "inventive_step_disc"),
    ("support", "support_score", "support_disc"),
    ("clarity", "clarity_score", "clarity_disc"),
    ("eligibility", "eligibility_score", "eligibility_disc"),
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def as_support_item(item: dict[str, Any], default_location: str) -> dict[str, str]:
    return {
        "location": str(item.get("source") or item.get("location") or default_location),
        "original_text": str(item.get("original_text") or ""),
        "translation": str(item.get("translation") or ""),
        "llm_evidence_explanation": str(item.get("llm_evidence_explanation") or ""),
    }


def text_key(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def evidence_source_item(issue: str, item: dict[str, Any], default_source: str) -> dict[str, str] | None:
    original_text = str(item.get("original_text") or "").strip()
    translation = str(item.get("translation") or "").strip()
    if not original_text or not translation:
        return None
    return {
        "issue": issue,
        "source": str(item.get("source") or item.get("location") or default_source),
        "original_text": original_text,
        "translation": translation,
        "llm_evidence_explanation": str(
            item.get("llm_evidence_explanation")
            or item.get("relevance")
            or "该审查材料原文是风险判断的依据。"
        ),
    }


def ensure_source_sentence_lists(data: dict[str, Any], limit: int = 5) -> bool:
    scores = data.get("dimension_scores") or {}
    evidence = data.get("evidence_trace") or {}
    evidence_by_issue: dict[str, list[dict[str, Any]]] = {}
    for item in evidence.get("examination_material_evidence") or []:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "").strip().lower()
        if issue:
            evidence_by_issue.setdefault(issue, []).append(item)

    def score_value(score_key: str) -> float:
        try:
            return float(scores.get(score_key))
        except (TypeError, ValueError):
            return 101.0

    candidates: list[dict[str, str]] = []
    for issue, score_key, disc_key in sorted(DIMENSION_SOURCE_KEYS, key=lambda item: score_value(item[1])):
        for evidence_item in evidence_by_issue.get(issue, []):
            candidate = evidence_source_item(issue, evidence_item, "examination_material_evidence")
            if candidate:
                candidates.append(candidate)
        disc = scores.get(disc_key)
        if isinstance(disc, dict):
            candidate = evidence_source_item(issue, disc, disc_key)
            if candidate:
                candidates.append(candidate)

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for candidate in candidates:
        key = text_key(candidate.get("original_text"))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
        if len(deduped) >= limit:
            break

    action_basis = [
        {
            **item,
            "llm_evidence_explanation": item.get("llm_evidence_explanation")
            or "该审查材料原文是后续处理或维持权利要求文本的依据。",
        }
        for item in deduped
    ]
    changed = data.get("risk_source_sentences") != deduped or data.get("action_basis_source_sentences") != action_basis
    data["risk_source_sentences"] = deduped
    data["action_basis_source_sentences"] = action_basis
    return changed


def ensure_specification_support(data: dict[str, Any]) -> bool:
    evidence = data.setdefault("evidence_trace", {})
    existing = evidence.get("specification_support")
    if isinstance(existing, list) and any(isinstance(item, dict) and item.get("original_text") for item in existing):
        return False

    support_items: list[dict[str, str]] = []
    support_disc = (data.get("dimension_scores") or {}).get("support_disc")
    if isinstance(support_disc, dict) and support_disc.get("original_text"):
        support_items.append(as_support_item(support_disc, "support_disc"))

    for item in evidence.get("examination_material_evidence") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("issue") or "").lower() == "support" and item.get("original_text"):
            support_items.append(as_support_item(item, "support evidence"))

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in support_items:
        key = item["original_text"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    evidence["specification_support"] = deduped
    return bool(deduped)


def missing_required_analysis_fields(data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    meta = data.get("meta") or {}
    for key in ["jurisdiction", "application_number", "title", "applicant", "filing_date", "examination_date", "outcome"]:
        if not meta.get(key):
            missing.append(f"meta.{key}")
    if not data.get("grant_label"):
        missing.append("grant_label")
    if data.get("aggregate_score") in ("", None):
        missing.append("aggregate_score")

    scores = data.get("dimension_scores") or {}
    for score_key, disc_key in DIMENSION_KEYS:
        if scores.get(score_key) in ("", None):
            missing.append(f"dimension_scores.{score_key}")
        disc = scores.get(disc_key)
        if not isinstance(disc, dict):
            missing.append(f"dimension_scores.{disc_key}")
            continue
        for field in ["analysis", "original_text", "translation", "llm_evidence_explanation"]:
            if not disc.get(field):
                missing.append(f"dimension_scores.{disc_key}.{field}")

    for key in ["top_risk_reasons", "recommended_actions"]:
        items = data.get(key)
        if not isinstance(items, list) or not items:
            missing.append(key)
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                if not (item.get("original_text") or item.get("english") or item.get("translation") or item.get("chinese")):
                    missing.append(f"{key}[{index}]")
            elif not item:
                missing.append(f"{key}[{index}]")

    for key in ["risk_source_sentences", "action_basis_source_sentences"]:
        items = data.get(key)
        if not isinstance(items, list) or not items:
            missing.append(key)
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                missing.append(f"{key}[{index}]")
                continue
            for field in ["issue", "source", "original_text", "translation", "llm_evidence_explanation"]:
                if not item.get(field):
                    missing.append(f"{key}[{index}].{field}")

    evidence = data.get("evidence_trace") or {}
    if not evidence.get("affected_claims"):
        missing.append("evidence_trace.affected_claims")
    if not evidence.get("examination_rounds"):
        missing.append("evidence_trace.examination_rounds")
    if not evidence.get("specification_support"):
        missing.append("evidence_trace.specification_support")
    if not evidence.get("examination_material_evidence"):
        missing.append("evidence_trace.examination_material_evidence")
    return missing


def main() -> None:
    parser = argparse.ArgumentParser(description="Ensure final analysis JSON has all fields rendered by the HTML report.")
    parser.add_argument("analysis_json", nargs="+")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    failed = False
    for raw_path in args.analysis_json:
        path = Path(raw_path)
        data = read_json(path)
        changed = False
        if not args.check_only:
            changed = ensure_specification_support(data)
            changed = ensure_source_sentence_lists(data) or changed
        if changed:
            write_json(path, data)
        missing = missing_required_analysis_fields(data)
        status = "ok" if not missing else "missing: " + ", ".join(missing)
        print(f"{path}: {status}")
        failed = failed or bool(missing)

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
