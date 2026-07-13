from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_app(value: Any) -> str:
    app = str(value or "").strip().upper().split(".")[0]
    if app and not app.startswith("EP"):
        app = f"EP{app}"
    return app


def load_pool_records(paths: list[Path]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        data = read_json(path)
        for record in data.get("records", []):
            app = normalize_app(record.get("application_number") or record.get("publication_number"))
            if not app:
                continue
            existing = records.setdefault(
                app,
                {
                    "application_number": app,
                    "publication_numbers": [],
                    "source_files": [],
                    "keyword_groups": [],
                    "benchmark_labels": [],
                    "title": "",
                },
            )
            pub = str(record.get("publication_number") or "").upper()
            if pub and pub not in existing["publication_numbers"]:
                existing["publication_numbers"].append(pub)
            if str(path) not in existing["source_files"]:
                existing["source_files"].append(str(path))
            group = str(record.get("keyword_group") or "")
            if group and group not in existing["keyword_groups"]:
                existing["keyword_groups"].append(group)
            label = str(record.get("benchmark_label") or "")
            if label and label not in existing["benchmark_labels"]:
                existing["benchmark_labels"].append(label)
            if not existing["title"] and record.get("title"):
                existing["title"] = str(record.get("title"))
    return records


def count_files(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    return len(list(path.rglob(pattern)))


def latest_rows_by_app(status_paths: list[Path]) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for path in status_paths:
        for row in read_csv(path):
            app = normalize_app(row.get("application_number"))
            if not app:
                continue
            copied = dict(row)
            copied["_status_file"] = str(path)
            latest[app] = copied
    return latest


def stage_original(case_dir: Path, status_row: dict[str, str] | None) -> str:
    original = case_dir / "original-application"
    pdf = count_files(original, "*.pdf")
    txt = count_files(original, "*.txt")
    html = count_files(original, "*.html")
    xml = count_files(original, "*.xml")
    zip_count = count_files(original, "*.zip")
    if pdf and txt:
        return "success_pdf_text"
    if pdf:
        return "success_pdf"
    if html or xml or zip_count:
        return "success_fallback"
    if status_row:
        status = str(status_row.get("status") or "")
        if status in {"publication_unavailable", "pdf_unavailable"}:
            return status
        if status == "error":
            return "failed"
    return "not_started"


def stage_docs(case_dir: Path) -> str:
    docs = case_dir / "docs"
    pdf = count_files(docs, "*.pdf")
    txt = count_files(docs, "*.txt")
    if pdf and txt:
        return "success_pdf_text"
    if pdf:
        return "success_pdf"
    if (case_dir / "register").exists():
        return "register_seen_no_docs"
    return "not_started"


def stage_processing(case_dir: Path, app: str) -> str:
    if (case_dir / f"{app}-benchmark-input.json").exists():
        return "benchmark_input_ready"
    if count_files(case_dir / "original-application", "*.txt") or count_files(case_dir / "docs", "*.txt"):
        return "source_text_ready"
    return "not_started"


def overall_status(original_status: str, docs_status: str, processing_status: str, started: bool) -> str:
    original_ok = original_status.startswith("success_")
    docs_ok = docs_status.startswith("success_")
    processing_ok = processing_status == "benchmark_input_ready"
    if original_ok and docs_ok and processing_ok:
        return "full_success"
    if original_ok or docs_ok or processing_ok:
        return "partial_success"
    if started:
        return "failed"
    return "not_started"


def build_record(app: str, pool_record: dict[str, Any], output_root: Path, pub_status: dict[str, str] | None) -> dict[str, Any]:
    case_dir = output_root / app
    original_dir = case_dir / "original-application"
    docs_dir = case_dir / "docs"
    register_dir = case_dir / "register"
    started = case_dir.exists() or bool(pub_status)
    original_status = stage_original(case_dir, pub_status)
    docs_status = stage_docs(case_dir)
    processing_status = stage_processing(case_dir, app)
    return {
        "application_number": app,
        "publication_numbers": pool_record.get("publication_numbers", []),
        "title": pool_record.get("title", ""),
        "keyword_groups": pool_record.get("keyword_groups", []),
        "benchmark_labels": pool_record.get("benchmark_labels", []),
        "source_files": pool_record.get("source_files", []),
        "started": started,
        "overall_status": overall_status(original_status, docs_status, processing_status, started),
        "stages": {
            "original_publication": original_status,
            "file_wrapper_docs": docs_status,
            "local_processing": processing_status,
        },
        "artifacts": {
            "case_dir_exists": case_dir.exists(),
            "register_main_html": count_files(register_dir, "*-main.html"),
            "register_doclist_csv": count_files(register_dir, "*-doclist.csv"),
            "docs_pdf": count_files(docs_dir, "*.pdf"),
            "docs_txt": count_files(docs_dir, "*.txt"),
            "original_pdf": count_files(original_dir, "*.pdf"),
            "original_txt": count_files(original_dir, "*.txt"),
            "original_html": count_files(original_dir, "*.html"),
            "original_xml": count_files(original_dir, "*.xml"),
            "original_zip": count_files(original_dir, "*.zip"),
            "benchmark_input": (case_dir / f"{app}-benchmark-input.json").exists(),
        },
        "latest_publication_fetch_status": pub_status or {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain a JSON ledger of all EP numbers started for collection.")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--pool", action="append", default=[], help="Manifest/candidate JSON to include in the ledger. Repeatable.")
    parser.add_argument("--status-glob", action="append", default=["_state/publication-server*.csv"], help="Status CSV glob under output root. Repeatable.")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    output_root = project_root / args.output_root
    pool_paths = [project_root / path for path in args.pool]
    pool_records = load_pool_records(pool_paths)

    for case_dir in output_root.glob("EP*"):
        if case_dir.is_dir():
            pool_records.setdefault(
                case_dir.name,
                {
                    "application_number": case_dir.name,
                    "publication_numbers": [],
                    "source_files": [],
                    "keyword_groups": [],
                    "benchmark_labels": [],
                    "title": "",
                },
            )

    status_paths: list[Path] = []
    for pattern in args.status_glob:
        status_paths.extend(output_root.glob(pattern))
    pub_status = latest_rows_by_app(sorted(set(status_paths)))

    for app in pub_status:
        pool_records.setdefault(
            app,
            {
                "application_number": app,
                "publication_numbers": [],
                "source_files": [],
                "keyword_groups": [],
                "benchmark_labels": [],
                "title": "",
            },
        )

    records = [build_record(app, pool_records[app], output_root, pub_status.get(app)) for app in sorted(pool_records)]
    summary = {
        "total_records_in_ledger": len(records),
        "started": sum(1 for row in records if row["started"]),
        "overall_status": dict(Counter(row["overall_status"] for row in records)),
        "original_publication": dict(Counter(row["stages"]["original_publication"] for row in records)),
        "file_wrapper_docs": dict(Counter(row["stages"]["file_wrapper_docs"] for row in records)),
        "local_processing": dict(Counter(row["stages"]["local_processing"] for row in records)),
    }
    payload = {
        "metadata": {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "output_root": str(output_root),
            "pools": [str(path) for path in pool_paths],
            "status_files": [str(path) for path in sorted(set(status_paths))],
            "status_definition": {
                "full_success": "original_publication, file_wrapper_docs, and benchmark_input are all present.",
                "partial_success": "at least one major artifact or benchmark input exists, but not all stages are complete.",
                "failed": "collection was attempted but no usable major artifact exists.",
                "not_started": "no local case directory or fetch status has been observed.",
            },
        },
        "summary": summary,
        "records": records,
    }
    output = project_root / args.output if args.output else output_root / "_state" / "collection-ledger.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote ledger: {output}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

