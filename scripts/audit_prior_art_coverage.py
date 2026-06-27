from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from build_benchmark_input import (
    collect_case_document_texts,
    extract_prior_art,
    extract_register_citations_block,
    find_main_file,
    normalize_patent_publication,
    read_text,
)


FIELDS = [
    "application_number",
    "listed_prior_art_count",
    "examined_prior_art_count",
    "coverage_ok",
    "hallucination_ok",
    "local_pdf_ok",
    "missing_examined_refs",
    "hallucinated_listed_refs",
    "missing_local_pdf_refs",
    "unresolved_pdf_refs",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def publication_key(citation: str) -> str:
    publication = normalize_patent_publication(citation)
    if not publication:
        return ""
    return strip_kind_code(publication)


def strip_kind_code(publication: str) -> str:
    return re.sub(r"[A-Z]\d?$", "", publication.upper())


def collect_listed_prior_art(benchmark_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    listed: dict[str, dict[str, Any]] = {}
    for item in ((benchmark_input.get("benchmark_input") or {}).get("prior_art_docs") or []):
        if not isinstance(item, dict):
            continue
        key = publication_key(str(item.get("citation") or item.get("publication_number") or ""))
        if key:
            listed.setdefault(key, item)
    return listed


def collect_examined_prior_art(case_dir: Path, application_number: str) -> dict[str, dict[str, Any]]:
    _, documents = collect_case_document_texts(case_dir)
    main_file = find_main_file(case_dir, application_number)
    if main_file:
        register_citations = extract_register_citations_block(read_text(main_file))
        if register_citations.strip():
            documents = list(documents) + [
                {"name": f"{main_file.name}#documents_cited", "text": register_citations}
            ]
    extracted = extract_prior_art(
        documents,
        [],
        1000,
        {"application_number": application_number},
        {},
    )
    examined: dict[str, dict[str, Any]] = {}
    for item in extracted:
        key = publication_key(str(item.get("citation") or ""))
        if key:
            examined.setdefault(key, item)
    return examined


def existing_local_pdf(case_dir: Path, value: Any) -> bool:
    if not value:
        return False
    path = Path(str(value))
    if not path.is_absolute():
        path = case_dir / path
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"%PDF"
    except OSError:
        return False


def audit_file(path: Path) -> dict[str, str]:
    data = read_json(path)
    case_dir = path.parent
    app = str(data.get("application_number") or case_dir.name).split(".")[0]
    listed = collect_listed_prior_art(data)
    examined = collect_examined_prior_art(case_dir, app)

    missing_examined = sorted(key for key in examined if key not in listed)
    hallucinated = sorted(key for key in listed if key not in examined)
    missing_local_pdf = sorted(
        key
        for key, item in listed.items()
        if not existing_local_pdf(case_dir, item.get("local_pdf") or item.get("pdf_path"))
    )
    unresolved_pdf = sorted(
        key
        for key, item in listed.items()
        if str(item.get("pdf_download_status") or "") not in {"downloaded", "existing"}
    )

    return {
        "application_number": app,
        "listed_prior_art_count": str(len(listed)),
        "examined_prior_art_count": str(len(examined)),
        "coverage_ok": str(not missing_examined),
        "hallucination_ok": str(not hallucinated),
        "local_pdf_ok": str(not missing_local_pdf),
        "missing_examined_refs": "; ".join(missing_examined),
        "hallucinated_listed_refs": "; ".join(hallucinated),
        "missing_local_pdf_refs": "; ".join(missing_local_pdf),
        "unresolved_pdf_refs": "; ".join(unresolved_pdf),
    }


def iter_benchmark_inputs(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("*-benchmark-input.json"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit prior-art coverage against local examination text.")
    parser.add_argument("root", help="Benchmark input JSON, case directory, or nested root containing benchmark inputs.")
    parser.add_argument("-o", "--output", help="CSV output path.")
    args = parser.parse_args()

    root = Path(args.root)
    rows = [audit_file(path) for path in iter_benchmark_inputs(root)]
    if not rows:
        raise SystemExit(f"No benchmark input JSON files found under {root}")

    output = Path(args.output) if args.output else root / "_prior_art_coverage_audit.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    for row in rows:
        print(
            f"{row['application_number']}: coverage_ok={row['coverage_ok']}, "
            f"hallucination_ok={row['hallucination_ok']}, local_pdf_ok={row['local_pdf_ok']}"
        )
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
