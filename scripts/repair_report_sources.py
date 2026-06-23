from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from verify_report_sources import best_match, normalize_text, source_documents


PAGE_MARKER_PATTERN = re.compile(r"---\s*PAGE\s+[0-9]+\s*---", re.IGNORECASE)


def meaningful_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if PAGE_MARKER_PATTERN.fullmatch(line):
            continue
        if line.upper().startswith("SOURCE:"):
            continue
        lines.append(line)
    return lines


def source_texts(case_dir: Path) -> dict[Path, str]:
    texts: dict[Path, str] = {}
    for path in sorted(case_dir.rglob("*.txt")):
        if path.name.startswith("_tmp") or path.name.endswith(".prompt.txt") or "dryrun.prompt" in path.name:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if normalize_text(text):
            texts[path] = text
    return texts


def preferred_paths(item: dict[str, Any], texts: dict[Path, str]) -> list[Path]:
    source = str(item.get("source") or item.get("location") or "")
    if not source:
        return []
    source_name = Path(source.replace("\\", "/")).name
    return [path for path in texts if path.name == source_name or path.stem == Path(source_name).stem]


def best_source_excerpt(query: str, paths: list[Path], texts: dict[Path, str], max_lines: int) -> tuple[Path | None, str, float]:
    query_norm = normalize_text(query)
    if not query_norm:
        return None, "", 0.0

    best_path: Path | None = None
    best_excerpt = ""
    best_score = 0.0
    for path in paths:
        lines = meaningful_lines(texts[path])
        for start in range(len(lines)):
            end_limit = min(len(lines), start + max_lines)
            for end in range(start + 1, end_limit + 1):
                excerpt = "\n".join(lines[start:end])
                normalized = normalize_text(excerpt)
                if not normalized:
                    continue
                score = SequenceMatcher(None, query_norm, normalized).ratio()
                if score > best_score:
                    best_path = path
                    best_excerpt = excerpt
                    best_score = score
                if score >= 0.98:
                    return best_path, best_excerpt, best_score
    return best_path, best_excerpt, best_score


def walk_original_text_items(value: Any, path: str = "") -> list[dict[str, Any]]:
    if path.startswith("top_risk_reasons") or path.startswith("recommended_actions"):
        return []
    items: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("original_text"), str):
            items.append(value)
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            items.extend(walk_original_text_items(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            items.extend(walk_original_text_items(item, f"{path}[{index}]"))
    return items


def repair(analysis_path: Path, case_dir: Path, min_verified_score: float, min_repair_score: float, max_lines: int) -> int:
    data = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
    documents = source_documents(case_dir)
    texts = source_texts(case_dir)
    all_paths = list(texts)
    repaired = 0

    for item in walk_original_text_items(data):
        original = str(item.get("original_text") or "")
        _, score = best_match(original, documents)
        if score >= min_verified_score:
            continue

        paths = preferred_paths(item, texts) or all_paths
        path, excerpt, repair_score = best_source_excerpt(original, paths, texts, max_lines)
        if path and excerpt and repair_score >= min_repair_score:
            item["original_text"] = excerpt
            if not item.get("source"):
                item["source"] = path.name
            repaired += 1

    if repaired:
        analysis_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace weak report original_text values with exact contiguous excerpts from local source TXT files.")
    parser.add_argument("analysis_json")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--min-verified-score", type=float, default=0.88)
    parser.add_argument("--min-repair-score", type=float, default=0.55)
    parser.add_argument("--max-lines", type=int, default=10)
    args = parser.parse_args()

    repaired = repair(
        Path(args.analysis_json),
        Path(args.case_dir),
        args.min_verified_score,
        args.min_repair_score,
        args.max_lines,
    )
    print(f"Repaired original_text fields: {repaired}")


if __name__ == "__main__":
    main()
