from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def normalize_text(value: str) -> str:
    value = re.sub(r"---\s*PAGE\s+[0-9]+\s*---", " ", value, flags=re.I)
    value = re.sub(r"^\s*SOURCE:.*$", " ", value, flags=re.I | re.M)
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\u2018\u2019]", "'", value)
    value = re.sub(r"[\u201c\u201d]", '"', value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def iter_original_texts(data: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if path.startswith("top_risk_reasons") or path.startswith("recommended_actions"):
            return
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key == "original_text":
                    fields.append((child_path, str(item or "")))
                else:
                    walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(data, "")
    return fields


def source_documents(case_dir: Path) -> list[tuple[Path, str, str]]:
    documents: list[tuple[Path, str, str]] = []
    for path in sorted(case_dir.rglob("*.txt")):
        if path.name.startswith("_tmp") or path.name.endswith(".prompt.txt") or "dryrun.prompt" in path.name:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if text.strip():
            normalized = normalize_text(text)
            if normalized:
                documents.append((path, normalized, compact_text(text)))
    return documents


def best_match(value: str, documents: list[tuple[Path, str, str]]) -> tuple[Path | None, float]:
    query = normalize_text(value)
    if not query:
        return None, 0.0
    if len(query) < 8:
        return None, 0.0

    for path, normalized, compacted in documents:
        if query in normalized:
            return path, 1.0
        compact_query = compact_text(value)
        if len(compact_query) >= 30 and compact_query in compacted:
            return path, 0.97

    words = query.split()
    anchors = []
    if len(words) >= 12:
        anchors = [" ".join(words[:12]), " ".join(words[-12:])]

    best_path: Path | None = None
    best_score = 0.0
    for path, normalized, compacted in documents:
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
