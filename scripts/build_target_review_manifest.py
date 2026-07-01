from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXAM_DOC_RE = re.compile(
    r"European search opinion|Supplementary European search report|Copy of the international search report|"
    r"Written opinion of the ISA|Copy of the international preliminary report on patentability|"
    r"Communication from the Examining Division|Annex to the communication|"
    r"Reply to communication from the Examining Division|Amended claims|Communication about intention to grant|"
    r"Decision to grant|refus|withdrawn",
    re.I,
)
ORIGINAL_DOC_RE = re.compile(
    r"Application documents|Request for grant of a European patent|Description|Claims|Drawings|Abstract|"
    r"Published international application|Bibliographic data of the European patent application",
    re.I,
)

STATUS_FIELDS = [
    "index",
    "application_number",
    "publication_number",
    "keyword_group",
    "benchmark_label",
    "status",
    "doclist_total",
    "matched_exam_doc_count",
    "matched_original_doc_count",
    "error",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_app(value: Any) -> str:
    app = str(value or "").strip().upper().split(".")[0]
    if app and not app.startswith("EP"):
        app = f"EP{app}"
    return app


def load_candidate_records(path: Path, max_candidates: int) -> list[dict[str, Any]]:
    data = read_json(path)
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for record in data.get("records", []):
        app = normalize_app(record.get("application_number"))
        if not app or app in seen:
            continue
        seen.add(app)
        copied = dict(record)
        copied["application_number"] = app
        records.append(copied)
        if max_candidates and len(records) >= max_candidates:
            break
    return records


def read_doclist_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def fetch_doclist(
    app: str,
    cache_root: Path,
    project_root: Path,
    retry_count: int,
    retry_delay_seconds: int,
    use_cached: bool,
) -> Path:
    app_cache = cache_root / app
    app_cache.mkdir(parents=True, exist_ok=True)
    args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "scripts\\fetch-epo-doclist.ps1",
        "-ApplicationNumber",
        app,
        "-OutputDir",
        str(app_cache),
        "-RetryCount",
        str(retry_count),
        "-RetryDelaySeconds",
        str(retry_delay_seconds),
    ]
    if use_cached:
        args.append("-UseCached")
    subprocess.run(args, cwd=str(project_root), check=True)
    return app_cache / f"{app}-doclist.csv"


def build_manifest_record(
    record: dict[str, Any],
    rows: list[dict[str, str]],
    exam_rows: list[dict[str, str]],
    original_rows: list[dict[str, str]],
) -> dict[str, Any]:
    app = normalize_app(record.get("application_number"))
    publication = str(record.get("publication_number") or "").upper()
    doclist_url = str(
        record.get("epo_doclist_url")
        or record.get("epo_register_doclist_url")
        or f"https://register.epo.org/application?number={app}&lng=en&tab=doclist"
    )
    main_url = str(
        record.get("epo_register_main_url")
        or f"https://register.epo.org/application?number={app}&lng=en&tab=main"
    )
    matched_queries = record.get("matched_queries") or []
    matched_query = "; ".join(str(item) for item in matched_queries) if isinstance(matched_queries, list) else str(matched_queries)
    return {
        "id": app,
        "jurisdiction": "EP",
        "application_number": app,
        "application_number_raw": str(record.get("application_number") or app),
        "publication_number": publication,
        "title": str(record.get("title") or ""),
        "assignee": str(record.get("assignee") or ""),
        "priority_date": str(record.get("priority_date") or ""),
        "filing_date": str(record.get("filing_date") or ""),
        "publication_date": str(record.get("publication_date") or ""),
        "keyword_group": str(record.get("keyword_group") or ""),
        "keyword_category": str(record.get("keyword_category") or record.get("category") or ""),
        "matched_query": matched_query or str(record.get("source_query") or ""),
        "benchmark_label": str(record.get("benchmark_label") or ""),
        "google_status": str(record.get("google_legal_status") or record.get("google_status") or ""),
        "google_patents_url": str(record.get("google_patents_url") or ""),
        "epo_doclist_url": doclist_url,
        "epo_register_main_url": main_url,
        "links": {
            "google_patents": str(record.get("google_patents_url") or ""),
            "epo_register_main": main_url,
            "epo_register_doclist": doclist_url,
        },
        "verification_level": "doclist_exam_and_original_verified",
        "doclist_total": len(rows),
        "matched_exam_doc_count": len(exam_rows),
        "matched_exam_docs_sample": [
            {
                "date": row.get("date", ""),
                "title": row.get("title", ""),
                "documentId": row.get("documentId", ""),
                "pages": row.get("pages", ""),
            }
            for row in exam_rows[:8]
        ],
        "matched_original_doc_count": len(original_rows),
        "matched_original_docs_sample": [
            {
                "date": row.get("date", ""),
                "title": row.get("title", ""),
                "documentId": row.get("documentId", ""),
                "pages": row.get("pages", ""),
            }
            for row in original_rows[:5]
        ],
        "download": {
            "pipeline": "scripts/run_epo_benchmark.ps1",
            "stage": "collect",
        },
    }


def write_status(status_path: Path, rows: list[dict[str, str]]) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with status_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: int(row.get("index") or 0)))


def validate_one(
    index: int,
    record: dict[str, Any],
    args: argparse.Namespace,
    project_root: Path,
    cache_root: Path,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    app = normalize_app(record.get("application_number"))
    row = {
        "index": str(index),
        "application_number": app,
        "publication_number": str(record.get("publication_number") or ""),
        "keyword_group": str(record.get("keyword_group") or ""),
        "benchmark_label": str(record.get("benchmark_label") or ""),
        "status": "rejected",
        "doclist_total": "0",
        "matched_exam_doc_count": "0",
        "matched_original_doc_count": "0",
        "error": "",
    }
    try:
        csv_path = fetch_doclist(
            app,
            cache_root,
            project_root,
            args.retry_count,
            args.retry_delay_seconds,
            args.use_cached,
        )
        rows = read_doclist_csv(csv_path)
        exam_rows = [item for item in rows if EXAM_DOC_RE.search(str(item.get("title") or ""))]
        original_rows = [item for item in rows if ORIGINAL_DOC_RE.search(str(item.get("title") or ""))]
        row["doclist_total"] = str(len(rows))
        row["matched_exam_doc_count"] = str(len(exam_rows))
        row["matched_original_doc_count"] = str(len(original_rows))
        if len(exam_rows) < args.min_exam_docs:
            row["error"] = f"matched_exam_doc_count<{args.min_exam_docs}"
            return row, None
        if len(original_rows) < args.min_original_docs:
            row["error"] = f"matched_original_doc_count<{args.min_original_docs}"
            return row, None
        row["status"] = "accepted"
        return row, build_manifest_record(record, rows, exam_rows, original_rows)
    except Exception as exc:
        row["status"] = "error"
        row["error"] = repr(exc)
        return row, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a verified EPO review-file manifest from target keyword candidates.")
    parser.add_argument("--candidates", default="markush-run/benchmark/ep_application_candidates_target_pool.json")
    parser.add_argument("--output", default="markush-run/benchmark/ep_review_file_sources_target500.json")
    parser.add_argument("--target", type=int, default=500, help="Stop once this many verified records are accepted.")
    parser.add_argument("--max-candidates", type=int, default=0, help="Candidate cap before validation. 0 means all records.")
    parser.add_argument("--cache-root", default="markush-run/benchmark/target500-doclist-cache")
    parser.add_argument("--status-file", default="", help="Validation CSV. Defaults beside output.")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--min-exam-docs", type=int, default=1)
    parser.add_argument("--min-original-docs", type=int, default=1)
    parser.add_argument("--retry-count", type=int, default=4)
    parser.add_argument("--retry-delay-seconds", type=int, default=3)
    parser.add_argument("--request-delay-seconds", type=float, default=0.0)
    parser.add_argument("--use-cached", dest="use_cached", action="store_true")
    parser.add_argument("--no-use-cached", dest="use_cached", action="store_false")
    parser.set_defaults(use_cached=True)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    candidates_path = project_root / args.candidates
    output_path = project_root / args.output
    cache_root = project_root / args.cache_root
    status_path = project_root / args.status_file if args.status_file else output_path.with_suffix(".validation-status.csv")

    records = load_candidate_records(candidates_path, args.max_candidates)
    if not records:
        raise SystemExit(f"No candidate records with application_number found in {candidates_path}")

    accepted: list[dict[str, Any]] = []
    status_rows: list[dict[str, str]] = []
    workers = max(1, args.workers)
    indexed_records = list(enumerate(records, start=1))
    print(f"Validating {len(indexed_records)} candidates with {workers} worker(s). Target={args.target}", flush=True)

    pending = iter(indexed_records)
    in_flight = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        def submit_next() -> bool:
            try:
                index, record = next(pending)
            except StopIteration:
                return False
            if args.request_delay_seconds:
                time.sleep(args.request_delay_seconds)
            future = executor.submit(validate_one, index, record, args, project_root, cache_root)
            in_flight[future] = index
            return True

        for _ in range(workers):
            if not submit_next():
                break

        while in_flight and (not args.target or len(accepted) < args.target):
            for future in as_completed(list(in_flight)):
                in_flight.pop(future)
                row, manifest_record = future.result()
                status_rows.append(row)
                if manifest_record:
                    accepted.append(manifest_record)
                write_status(status_path, status_rows)
                print(
                    f"[{row['status']}] {row['application_number']}: "
                    f"exam={row['matched_exam_doc_count']} original={row['matched_original_doc_count']} "
                    f"accepted={len(accepted)}",
                    flush=True,
                )
                if args.target and len(accepted) >= args.target:
                    break
                submit_next()
                break

    selected = accepted[: args.target] if args.target else accepted
    stats = {
        "total_records": len(selected),
        "labels": dict(Counter(str(row.get("benchmark_label") or "") for row in selected)),
        "keyword_groups": dict(Counter(str(row.get("keyword_group") or "") for row in selected)),
    }
    output = {
        "metadata": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(candidates_path),
            "note": "Target-keyword EP candidates verified through EPO Register doclist before benchmark collection.",
            "target_requested": args.target,
            "validated_candidates": len(status_rows),
            "accepted_records": len(selected),
            "min_exam_docs": args.min_exam_docs,
            "min_original_docs": args.min_original_docs,
            "validation_status_file": str(status_path.relative_to(project_root)),
            "doclist_cache_root": str(cache_root.relative_to(project_root)),
        },
        "stats": stats,
        "records": selected,
    }
    write_json(output_path, output)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"Wrote manifest: {output_path}")
    print(f"Wrote validation status: {status_path}")
    if args.target and len(selected) < args.target:
        print(f"WARNING: accepted {len(selected)}/{args.target}; enlarge the candidate pool and rerun.", file=sys.stderr)


if __name__ == "__main__":
    main()
