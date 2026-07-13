from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


FIELDS = [
    "application_number",
    "in_manifest",
    "case_dir_exists",
    "doclist_rows",
    "docs_index_rows",
    "docs_valid_pdfs",
    "original_index_rows",
    "original_valid_pdfs",
    "benchmark_input_exists",
    "prior_art_total",
    "prior_art_local_pdfs",
    "prior_art_unresolved",
    "is_complete",
    "issues",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"%PDF"
    except OSError:
        return False


def resolve_index_pdf(case_dir: Path, row: dict[str, str], folder: Path) -> Path:
    raw_path = row.get("path") or ""
    if raw_path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        candidates = [case_dir / path, folder / path]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return folder / str(row.get("fileName") or "")


def count_valid_index_pdfs(case_dir: Path, folder: Path) -> tuple[int, int]:
    rows = read_csv_rows(folder / "download-index.csv")
    valid = sum(1 for row in rows if valid_pdf(resolve_index_pdf(case_dir, row, folder)))
    return len(rows), valid


def local_pdf_ok(case_dir: Path, value: Any) -> bool:
    if not value:
        return False
    path = Path(str(value))
    if not path.is_absolute():
        path = case_dir / path
    return valid_pdf(path)


def prior_art_counts(case_dir: Path, benchmark_input: Path) -> tuple[int, int, int]:
    if not benchmark_input.exists():
        return 0, 0, 0
    data = read_json(benchmark_input)
    docs = ((data.get("benchmark_input") or {}).get("prior_art_docs") or [])
    total = local = unresolved = 0
    for item in docs:
        if not isinstance(item, dict):
            continue
        total += 1
        status = str(item.get("pdf_download_status") or "")
        if status in {"downloaded", "existing"} and local_pdf_ok(
            case_dir,
            item.get("pdf_local_path") or item.get("local_pdf") or item.get("pdf_path"),
        ):
            local += 1
        else:
            unresolved += 1
    return total, local, unresolved


def manifest_apps(path: Path | None) -> list[str]:
    if not path:
        return []
    data = read_json(path)
    apps = []
    for record in data.get("records", []):
        app = str(record.get("application_number") or "").split(".")[0]
        if app:
            apps.append(app)
    return apps


def case_dirs(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir() and path.name.upper().startswith("EP"))


def audit_case(root: Path, app: str, in_manifest: bool) -> dict[str, str]:
    case_dir = root / app
    benchmark_input = case_dir / f"{app}-benchmark-input.json"
    doclist_rows = read_csv_rows(case_dir / "register" / f"{app}-doclist.csv")
    docs_index_rows, docs_valid_pdfs = count_valid_index_pdfs(case_dir, case_dir / "docs")
    original_index_rows, original_valid_pdfs = count_valid_index_pdfs(case_dir, case_dir / "original-application")
    prior_total, prior_local, prior_unresolved = prior_art_counts(case_dir, benchmark_input)

    issues: list[str] = []
    if not case_dir.exists():
        issues.append("missing_case_dir")
    if not doclist_rows:
        issues.append("missing_doclist")
    if docs_index_rows < 1 or docs_valid_pdfs < 1:
        issues.append("missing_review_docs")
    if original_index_rows < 1 or original_valid_pdfs < 1:
        issues.append("missing_original_application")
    if not benchmark_input.exists():
        issues.append("missing_benchmark_input")
    if prior_total and prior_unresolved:
        issues.append("unresolved_prior_art_pdf")
    is_complete = not any(
        issue
        for issue in issues
        if issue
        in {
            "missing_case_dir",
            "missing_doclist",
            "missing_review_docs",
            "missing_original_application",
            "missing_benchmark_input",
        }
    )
    return {
        "application_number": app,
        "in_manifest": str(in_manifest),
        "case_dir_exists": str(case_dir.exists()),
        "doclist_rows": str(len(doclist_rows)),
        "docs_index_rows": str(docs_index_rows),
        "docs_valid_pdfs": str(docs_valid_pdfs),
        "original_index_rows": str(original_index_rows),
        "original_valid_pdfs": str(original_valid_pdfs),
        "benchmark_input_exists": str(benchmark_input.exists()),
        "prior_art_total": str(prior_total),
        "prior_art_local_pdfs": str(prior_local),
        "prior_art_unresolved": str(prior_unresolved),
        "is_complete": str(is_complete),
        "issues": "; ".join(issues),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit downloaded raw materials for an EPO benchmark case set.")
    parser.add_argument("root", help="Case-set root, e.g. markush-run/benchmark-target500.")
    parser.add_argument("--manifest", default="", help="Optional manifest to include missing expected apps.")
    parser.add_argument("-o", "--output", default="", help="CSV output path. Defaults to <root>/_raw_materials_audit.csv.")
    parser.add_argument("--summary", default="", help="JSON summary path. Defaults to <root>/_raw_materials_audit_summary.json.")
    args = parser.parse_args()

    root = Path(args.root)
    manifest_path = Path(args.manifest) if args.manifest else None
    manifest_list = manifest_apps(manifest_path) if manifest_path else []
    manifest_set = set(manifest_list)
    apps = list(dict.fromkeys(manifest_list + [path.name for path in case_dirs(root)]))
    if not apps:
        raise SystemExit(f"No EP case directories or manifest records found for {root}")

    rows = [audit_case(root, app, app in manifest_set) for app in apps]
    output = Path(args.output) if args.output else root / "_raw_materials_audit.csv"
    summary_path = Path(args.summary) if args.summary else root / "_raw_materials_audit_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    issue_counter: Counter[str] = Counter()
    for row in rows:
        for issue in filter(None, (row["issues"] or "").split("; ")):
            issue_counter[issue] += 1
    summary = {
        "root": str(root),
        "manifest": str(manifest_path) if manifest_path else "",
        "total_rows": len(rows),
        "complete_cases": sum(1 for row in rows if row["is_complete"] == "True"),
        "cases_with_unresolved_prior_art": sum(1 for row in rows if int(row["prior_art_unresolved"] or 0) > 0),
        "prior_art_total": sum(int(row["prior_art_total"] or 0) for row in rows),
        "prior_art_local_pdfs": sum(int(row["prior_art_local_pdfs"] or 0) for row in rows),
        "issues": dict(issue_counter),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote audit CSV: {output}")
    print(f"Wrote audit summary: {summary_path}")


if __name__ == "__main__":
    main()
