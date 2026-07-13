from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CASE_PREFIX = "EP"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob(pattern) if item.is_file())


def sum_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return sum(file_size(item) for item in path.rglob(pattern) if item.is_file())


def iter_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return [item for item in path.rglob("*") if item.is_file()]


def case_dirs(output_root: Path) -> list[Path]:
    return sorted(item for item in output_root.iterdir() if item.is_dir() and item.name.startswith(CASE_PREFIX))


def load_ledger_status(ledger_path: Path) -> dict[str, dict[str, Any]]:
    ledger = read_json(ledger_path)
    rows = ledger.get("records", [])
    return {str(row.get("application_number") or ""): row for row in rows}


def classify_case(case_dir: Path, ledger_row: dict[str, Any] | None) -> dict[str, Any]:
    original = case_dir / "original-application"
    docs = case_dir / "docs"
    register = case_dir / "register"
    assets = case_dir / "assets"
    prior_art = case_dir / "prior-art"

    original_pdf = count_files(original, "*.pdf")
    original_txt = count_files(original, "*.txt")
    original_html = count_files(original, "*.html")
    original_xml = count_files(original, "*.xml")
    original_zip = count_files(original, "*.zip")
    original_index = int((original / "download-index.csv").exists())
    original_source = int((original / "publication-server-source.json").exists())
    original_artifact = original_pdf + original_html + original_xml + original_zip

    docs_pdf = count_files(docs, "*.pdf")
    docs_txt = count_files(docs, "*.txt")
    docs_index = int((docs / "download-index.csv").exists())
    docs_artifact = docs_pdf

    register_main = count_files(register, "*-main.html")
    register_doclist_html = count_files(register, "*-doclist.html")
    register_doclist_csv = count_files(register, "*-doclist.csv")
    register_artifact = register_main + register_doclist_html + register_doclist_csv

    benchmark_input = count_files(case_dir, "*-benchmark-input.json")
    rendered_png = count_files(assets, "*.png")
    rendered_json = count_files(assets, "*.json")
    prior_art_files = len(iter_files(prior_art))

    total_files = len(iter_files(case_dir))
    total_bytes = sum(file_size(item) for item in iter_files(case_dir))
    original_bytes = sum(file_size(item) for item in iter_files(original))
    docs_bytes = sum(file_size(item) for item in iter_files(docs))
    register_bytes = sum(file_size(item) for item in iter_files(register))
    assets_bytes = sum(file_size(item) for item in iter_files(assets))
    prior_art_bytes = sum(file_size(item) for item in iter_files(prior_art))
    benchmark_bytes = sum_files(case_dir, "*-benchmark-input.json")

    ledger_status = str((ledger_row or {}).get("overall_status") or "")
    stages = (ledger_row or {}).get("stages") or {}

    if ledger_status == "full_success":
        primary_class = "A_strict_complete"
    elif original_pdf and original_txt and benchmark_input:
        primary_class = "B_original_text_and_benchmark"
    elif original_pdf and original_txt:
        primary_class = "C_original_pdf_text_ready"
    elif original_pdf:
        primary_class = "D_original_pdf_needs_text_extract"
    elif original_html or original_xml or original_zip:
        primary_class = "E_original_fallback_only"
    elif docs_artifact or docs_txt:
        primary_class = "F_file_wrapper_docs_only"
    elif register_artifact:
        primary_class = "G_register_metadata_only"
    elif total_files:
        primary_class = "H_misc_or_failed_artifacts"
    else:
        primary_class = "I_empty_case_dir"

    return {
        "case_id": case_dir.name,
        "primary_class": primary_class,
        "ledger_status": ledger_status,
        "ledger_original_stage": str(stages.get("original_publication") or ""),
        "ledger_docs_stage": str(stages.get("file_wrapper_docs") or ""),
        "ledger_processing_stage": str(stages.get("local_processing") or ""),
        "total_files": total_files,
        "total_mb": round(total_bytes / 1024 / 1024, 3),
        "original_pdf": original_pdf,
        "original_txt": original_txt,
        "original_html": original_html,
        "original_xml": original_xml,
        "original_zip": original_zip,
        "original_index": original_index,
        "original_source_json": original_source,
        "docs_pdf": docs_pdf,
        "docs_txt": docs_txt,
        "docs_index": docs_index,
        "register_main_html": register_main,
        "register_doclist_html": register_doclist_html,
        "register_doclist_csv": register_doclist_csv,
        "benchmark_input_json": benchmark_input,
        "rendered_png": rendered_png,
        "rendered_json": rendered_json,
        "prior_art_files": prior_art_files,
        "original_mb": round(original_bytes / 1024 / 1024, 3),
        "docs_mb": round(docs_bytes / 1024 / 1024, 3),
        "register_mb": round(register_bytes / 1024 / 1024, 3),
        "assets_mb": round(assets_bytes / 1024 / 1024, 3),
        "prior_art_mb": round(prior_art_bytes / 1024 / 1024, 3),
        "benchmark_mb": round(benchmark_bytes / 1024 / 1024, 3),
        "has_original_material": bool(original_artifact),
        "has_original_text": bool(original_txt),
        "has_docs_material": bool(docs_artifact or docs_txt),
        "has_register_metadata": bool(register_artifact),
        "has_local_processing": bool(benchmark_input or rendered_png),
    }


def classify_file_bucket(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    parts = rel.parts
    suffix = path.suffix.lower()
    if parts[0] in {"_logs", "_state"}:
        return parts[0]
    if not parts[0].startswith(CASE_PREFIX):
        return "root_status_or_audit"
    if len(parts) == 1:
        if path.name.endswith("-benchmark-input.json"):
            return "case_benchmark_input"
        return "case_root_misc"
    section = parts[1]
    if section == "original-application":
        if suffix == ".pdf":
            return "original_publication_pdf"
        if suffix == ".txt":
            return "original_publication_text"
        if suffix in {".html", ".xml", ".zip"}:
            return "original_publication_fallback"
        if suffix in {".csv", ".json"}:
            return "original_publication_metadata"
        return "original_publication_misc"
    if section == "docs":
        if suffix == ".pdf":
            return "file_wrapper_pdf"
        if suffix == ".txt":
            return "file_wrapper_text"
        if suffix == ".csv":
            return "file_wrapper_index"
        return "file_wrapper_misc"
    if section == "register":
        return "register_metadata"
    if section == "assets":
        if suffix == ".png":
            return "local_rendered_png"
        if suffix == ".json":
            return "local_rendered_json"
        return "local_asset_misc"
    if section == "prior-art":
        return "prior_art"
    return "case_other"


def file_bucket_summary(output_root: Path) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"file_count": 0, "bytes": 0, "extensions": Counter()})
    for path in output_root.rglob("*"):
        if not path.is_file():
            continue
        bucket = classify_file_bucket(output_root, path)
        buckets[bucket]["file_count"] += 1
        buckets[bucket]["bytes"] += file_size(path)
        buckets[bucket]["extensions"][path.suffix.lower() or "<none>"] += 1
    rows = []
    for bucket, data in sorted(buckets.items()):
        rows.append(
            {
                "bucket": bucket,
                "file_count": data["file_count"],
                "mb": round(data["bytes"] / 1024 / 1024, 3),
                "extensions": dict(data["extensions"].most_common()),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify collected benchmark artifacts without moving files.")
    parser.add_argument("--output-root", default="markush-run/benchmark-target500")
    parser.add_argument("--ledger", default="markush-run/benchmark-target500/_state/collection-ledger.json")
    parser.add_argument("--output-prefix", default="")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    output_root = project_root / args.output_root
    ledger_path = project_root / args.ledger
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefix = args.output_prefix or f"collection-classification-{stamp}"
    state_dir = output_root / "_state"

    ledger = load_ledger_status(ledger_path)
    rows = [classify_case(case_dir, ledger.get(case_dir.name)) for case_dir in case_dirs(output_root)]
    buckets = file_bucket_summary(output_root)

    class_counts = Counter(row["primary_class"] for row in rows)
    ledger_counts = Counter(row["ledger_status"] for row in rows)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_root": str(output_root),
        "ledger": str(ledger_path),
        "case_count": len(rows),
        "total_case_mb": round(sum(float(row["total_mb"]) for row in rows), 3),
        "primary_class_counts": dict(class_counts),
        "ledger_status_counts_for_existing_case_dirs": dict(ledger_counts),
        "material_counts": {
            "has_original_material": sum(1 for row in rows if row["has_original_material"]),
            "has_original_text": sum(1 for row in rows if row["has_original_text"]),
            "has_docs_material": sum(1 for row in rows if row["has_docs_material"]),
            "has_register_metadata": sum(1 for row in rows if row["has_register_metadata"]),
            "has_local_processing": sum(1 for row in rows if row["has_local_processing"]),
            "strict_complete": class_counts["A_strict_complete"],
        },
        "file_buckets": buckets,
        "class_definitions": {
            "A_strict_complete": "Ledger full_success: original publication, file-wrapper/register docs, and benchmark input are present.",
            "B_original_text_and_benchmark": "Original PDF text and benchmark input exist, but ledger is not full_success.",
            "C_original_pdf_text_ready": "Original publication PDF plus extracted text exist.",
            "D_original_pdf_needs_text_extract": "Original publication PDF exists but extracted text is missing.",
            "E_original_fallback_only": "Only Publication Server fallback artifact exists, such as HTML/XML/ZIP.",
            "F_file_wrapper_docs_only": "File-wrapper docs exist but original publication material is missing.",
            "G_register_metadata_only": "Register main/doclist metadata exists but no usable major documents are present.",
            "H_misc_or_failed_artifacts": "Some files exist, but they do not match the major material categories.",
            "I_empty_case_dir": "Case directory exists without files.",
        },
    }

    csv_path = state_dir / f"{prefix}.csv"
    json_path = state_dir / f"{prefix}.json"
    bucket_csv_path = state_dir / f"{prefix}-file-buckets.csv"
    latest_csv_path = state_dir / "collection-classification.csv"
    latest_json_path = state_dir / "collection-classification.json"
    latest_bucket_csv_path = state_dir / "collection-classification-file-buckets.csv"

    write_csv(csv_path, rows)
    write_csv(bucket_csv_path, buckets)
    write_csv(latest_csv_path, rows)
    write_csv(latest_bucket_csv_path, buckets)
    json_path.write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json_path.write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {bucket_csv_path}")


if __name__ == "__main__":
    main()

