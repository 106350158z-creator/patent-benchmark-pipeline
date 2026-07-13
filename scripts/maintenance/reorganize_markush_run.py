from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE_KEEP_NAMES = {"patents", "parsed_examples", "collection-index.json"}

CLASS_TO_FOLDER = {
    "A_strict_complete": ("complete", "A_full"),
    "B_original_text_and_benchmark": ("complete", "B_text_bench"),
    "C_original_pdf_text_ready": ("complete", "C_pdf_text"),
    "D_original_pdf_needs_text_extract": ("complete", "D_pdf_only"),
    "E_original_fallback_only": ("incomplete", "E_html_only"),
    "F_file_wrapper_docs_only": ("incomplete", "F_docs_only"),
    "G_register_metadata_only": ("incomplete", "G_register_only"),
    "H_misc_or_failed_artifacts": ("incomplete", "H_failed_misc"),
    "I_empty_case_dir": ("incomplete", "I_empty"),
}

USER_COMPLETE_CLASSES = {
    "A_strict_complete",
    "B_original_text_and_benchmark",
    "C_original_pdf_text_ready",
    "D_original_pdf_needs_text_extract",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def move_path(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists():
        raise FileExistsError(f"Target already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def path_for_case(benchmark_dir: Path, primary_class: str, case_id: str) -> Path:
    group, leaf = CLASS_TO_FOLDER.get(primary_class, ("incomplete", "Z_unclassified"))
    return benchmark_dir / "patents" / group / leaf / case_id


def move_existing_benchmark_contents(benchmark_dir: Path, backup_dir: Path) -> list[dict[str, str]]:
    moved: list[dict[str, str]] = []
    previous_dir = backup_dir / "benchmark_previous_contents"
    if not benchmark_dir.exists():
        benchmark_dir.mkdir(parents=True, exist_ok=True)
        return moved
    for item in sorted(benchmark_dir.iterdir(), key=lambda p: p.name.lower()):
        if item.name in ACTIVE_KEEP_NAMES:
            continue
        dst = previous_dir / item.name
        move_path(item, dst)
        moved.append({"from": str(item), "to": str(dst)})
    return moved


def move_top_level_legacy(markush_root: Path, benchmark_dir: Path, backup_dir: Path) -> list[dict[str, str]]:
    moved: list[dict[str, str]] = []
    skip = {benchmark_dir.resolve(), backup_dir.resolve()}
    for item in sorted(markush_root.iterdir(), key=lambda p: p.name.lower()):
        resolved = item.resolve()
        if resolved in skip:
            continue
        if item.name.startswith("_backup_before_reorganize_"):
            continue
        dst = backup_dir / "markush_run_previous_top_level" / item.name
        move_path(item, dst)
        moved.append({"from": str(item), "to": str(dst)})
    return moved


def case_index_row(row: dict[str, Any], old_path: Path, new_path: Path) -> dict[str, Any]:
    primary_class = str(row.get("primary_class") or "")
    return {
        "case_id": row.get("case_id"),
        "primary_class": primary_class,
        "complete_for_user_pdf_ocr_pipeline": primary_class in USER_COMPLETE_CLASSES,
        "strict_pipeline_complete": primary_class == "A_strict_complete",
        "incomplete_without_more_fetching": primary_class not in USER_COMPLETE_CLASSES,
        "old_path": str(old_path),
        "current_path": str(new_path),
        "ledger_status": row.get("ledger_status"),
        "ledger_original_stage": row.get("ledger_original_stage"),
        "ledger_docs_stage": row.get("ledger_docs_stage"),
        "ledger_processing_stage": row.get("ledger_processing_stage"),
        "counts": {
            "original_pdf": row.get("original_pdf", 0),
            "original_txt": row.get("original_txt", 0),
            "original_html": row.get("original_html", 0),
            "docs_pdf": row.get("docs_pdf", 0),
            "docs_txt": row.get("docs_txt", 0),
            "register_main_html": row.get("register_main_html", 0),
            "benchmark_input_json": row.get("benchmark_input_json", 0),
            "rendered_png": row.get("rendered_png", 0),
            "total_files": row.get("total_files", 0),
            "total_mb": row.get("total_mb", 0),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reorganize markush-run into a clean benchmark workspace and backup.")
    parser.add_argument("--markush-root", default="markush-run")
    parser.add_argument("--classification", default="markush-run/benchmark-target500/_state/collection-classification.json")
    parser.add_argument("--source-cases", default="markush-run/benchmark-target500")
    parser.add_argument("--disease-examples", default="markush-run/疾病分类9个样例")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    markush_root = project_root / args.markush_root
    benchmark_dir = markush_root / "benchmark"
    source_cases = project_root / args.source_cases
    classification_path = project_root / args.classification
    disease_examples = project_root / args.disease_examples

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = markush_root / f"_backup_before_reorganize_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    classification = read_json(classification_path)
    rows = classification.get("cases", [])
    if not rows:
        raise RuntimeError(f"No cases found in classification: {classification_path}")

    moved_benchmark_previous = move_existing_benchmark_contents(benchmark_dir, backup_dir)

    case_records: list[dict[str, Any]] = []
    moved_cases: list[dict[str, str]] = []
    missing_cases: list[str] = []
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if not case_id:
            continue
        src = source_cases / case_id
        dst = path_for_case(benchmark_dir, str(row.get("primary_class") or ""), case_id)
        if src.exists():
            move_path(src, dst)
            moved_cases.append({"from": str(src), "to": str(dst)})
        else:
            missing_cases.append(case_id)
        case_records.append(case_index_row(row, src, dst))

    parsed_examples_target = benchmark_dir / "parsed_examples" / "疾病分类9个样例"
    parsed_examples_record: dict[str, str] | None = None
    if disease_examples.exists():
        move_path(disease_examples, parsed_examples_target)
        parsed_examples_record = {"from": str(disease_examples), "to": str(parsed_examples_target)}

    top_level_backup = move_top_level_legacy(markush_root, benchmark_dir, backup_dir)

    class_counts = Counter(row["primary_class"] for row in case_records)
    user_complete = sum(1 for row in case_records if row["complete_for_user_pdf_ocr_pipeline"])
    strict_complete = sum(1 for row in case_records if row["strict_pipeline_complete"])
    incomplete = sum(1 for row in case_records if row["incomplete_without_more_fetching"])
    index = {
        "metadata": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "markush_root": str(markush_root),
            "benchmark_dir": str(benchmark_dir),
            "backup_dir": str(backup_dir),
            "source_classification": str(classification_path),
            "source_cases": str(source_cases),
            "note": "Cases were moved into benchmark/patents by class. The backup directory contains previous loose files, old manifests/caches, and residual logs/state.",
        },
        "definitions": {
            "complete_for_user_pdf_ocr_pipeline": "Has enough fetched material for the user's next-stage PDF/OCR/cutting workflow. Classes A/B/C/D are included; D has PDF but no extracted text yet.",
            "strict_pipeline_complete": "Class A only: original publication, file-wrapper/register docs, and benchmark input already exist.",
            "incomplete_without_more_fetching": "Classes E/F/G/H/I. These are not complete for the user's PDF/OCR workflow without fetching more source material.",
            "class_folders": {
                key: str(Path(*value)) for key, value in CLASS_TO_FOLDER.items()
            },
        },
        "summary": {
            "total_patents": len(case_records),
            "complete_for_user_pdf_ocr_pipeline": user_complete,
            "strict_pipeline_complete": strict_complete,
            "incomplete_without_more_fetching": incomplete,
            "class_counts": dict(class_counts),
            "moved_cases": len(moved_cases),
            "missing_source_case_dirs": len(missing_cases),
        },
        "parsed_examples": {
            "disease_9_examples": parsed_examples_record,
        },
        "cases": case_records,
        "moves": {
            "cases_sample": moved_cases[:20],
            "benchmark_previous_contents": moved_benchmark_previous,
            "top_level_backup": top_level_backup,
            "missing_source_case_dirs": missing_cases,
        },
    }

    index_path = benchmark_dir / "collection-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(index["summary"], ensure_ascii=False, indent=2))
    print(f"index={index_path}")
    print(f"backup={backup_dir}")


if __name__ == "__main__":
    main()

