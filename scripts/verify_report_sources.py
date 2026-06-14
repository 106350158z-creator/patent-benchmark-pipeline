import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def normalize_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\u2018\u2019]", "'", value)
    value = re.sub(r"[\u201c\u201d]", '"', value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def iter_original_texts(data: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    scores = data.get("dimension_scores") or {}
    for key, value in scores.items():
        if key.endswith("_disc") and isinstance(value, dict):
            fields.append((f"dimension_scores.{key}.original_text", str(value.get("original_text") or "")))

    evidence = data.get("evidence_trace") or {}
    for section in ["specification_support", "examination_material_evidence"]:
        for index, item in enumerate(evidence.get(section) or []):
            if isinstance(item, dict):
                fields.append((f"evidence_trace.{section}[{index}].original_text", str(item.get("original_text") or "")))
    return fields


def source_documents(case_dir: Path) -> list[tuple[Path, str]]:
    documents: list[tuple[Path, str]] = []
    for path in sorted(case_dir.rglob("*.txt")):
        if path.name.startswith("_tmp") or path.name.endswith(".prompt.txt") or "dryrun.prompt" in path.name:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if text.strip():
            documents.append((path, normalize_text(text)))
    return documents


def best_match(value: str, documents: list[tuple[Path, str]]) -> tuple[Path | None, float]:
    query = normalize_text(value)
    if not query:
        return None, 0.0

    for path, normalized in documents:
        if query in normalized:
            return path, 1.0

    words = query.split()
    anchors = []
    if len(words) >= 12:
        anchors = [" ".join(words[:12]), " ".join(words[-12:])]

    best_path: Path | None = None
    best_score = 0.0
    for path, normalized in documents:
        if anchors and all(anchor in normalized for anchor in anchors):
            return path, 0.92
        prefix = " ".join(words[:6])
        start = normalized.find(prefix) if prefix else -1
        if start < 0:
            continue
        window = normalized[max(0, start - 300) : start + len(query) + 300]
        score = SequenceMatcher(None, query, window).ratio()
        if score > best_score:
            best_path = path
            best_score = score
    return best_path, best_score


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify report original_text fields against local source documents.")
    parser.add_argument("analysis_json", help="Analysis JSON used by the HTML report.")
    parser.add_argument("--case-dir", required=True, help="Case directory containing source .txt files.")
    parser.add_argument("--min-score", type=float, default=0.90)
    args = parser.parse_args()

    analysis_path = Path(args.analysis_json)
    case_dir = Path(args.case_dir)
    data = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
    documents = source_documents(case_dir)
    failures = []

    for field, original_text in iter_original_texts(data):
        source, score = best_match(original_text, documents)
        has_manual_ellipsis = "..." in original_text
        if not original_text.strip() or has_manual_ellipsis or score < args.min_score:
            failures.append((field, score, source, "manual ellipsis" if has_manual_ellipsis else "no verified source match"))

    if failures:
        for field, score, source, reason in failures:
            print(f"FAIL {field} score={score:.2f} source={source} reason={reason}")
        raise SystemExit(1)

    print(f"OK {analysis_path}: verified {len(iter_original_texts(data))} original_text field(s) against {len(documents)} source file(s).")


if __name__ == "__main__":
    main()
