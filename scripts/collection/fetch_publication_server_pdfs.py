from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_FIELDS = [
    "index",
    "application_number",
    "publication_number",
    "status",
    "started_at_utc",
    "finished_at_utc",
    "pdf_path",
    "artifact_path",
    "formats",
    "url",
    "error",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_records(manifest: Path, limit: int, offset: int) -> list[dict[str, Any]]:
    data = read_json(manifest)
    records = [record for record in data.get("records", []) if record.get("application_number")]
    return records[offset : offset + limit if limit else None]


def normalize_publication_number(value: str) -> tuple[str, str, str]:
    text = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    match = re.fullmatch(r"EP0*([0-9]+)([A-Z][0-9]?)", text)
    if not match:
        raise ValueError(f"Unsupported EP publication number: {value}")
    digits, kind = match.groups()
    publication = f"EP{digits}{kind}"
    publication_server_id = f"EP{digits}NW{kind}"
    return publication, publication_server_id, kind


def is_valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5).startswith(b"%PDF-")
    except OSError:
        return False


def is_existing_artifact(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def existing_download_index_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def download_pdf(url: str, out_path: Path, timeout: int) -> None:
    headers = {
        "Accept": "application/pdf,*/*",
        "User-Agent": "patent-benchmark-pipeline/1.0",
    }
    request = urllib.request.Request(url, headers=headers)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    part_path.write_bytes(body)
    if not is_valid_pdf(part_path):
        quarantine = out_path.parent / "_quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        bad_path = quarantine / f"{out_path.stem}-response.bin"
        part_path.replace(bad_path)
        raise RuntimeError(f"Downloaded content is not a PDF: {url}")
    part_path.replace(out_path)


def download_artifact(url: str, out_path: Path, timeout: int) -> None:
    headers = {
        "Accept": "*/*",
        "User-Agent": "patent-benchmark-pipeline/1.0",
    }
    request = urllib.request.Request(url, headers=headers)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    part_path.write_bytes(body)
    if not is_existing_artifact(part_path):
        raise RuntimeError(f"Downloaded empty artifact: {url}")
    part_path.replace(out_path)


def read_url_text(url: str, timeout: int) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,*/*",
        "User-Agent": "patent-benchmark-pipeline/1.0",
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc


def available_formats(patent_url: str, timeout: int) -> set[str]:
    html = read_url_text(patent_url, timeout)
    return {match.group(1).lower() for match in re.finditer(r"document\.(xml|html|pdf|zip)", html, re.I)}


def fetch_record(
    index: int,
    record: dict[str, Any],
    output_root: Path,
    timeout: int,
    skip_existing: bool,
    fallback_formats: list[str],
) -> dict[str, str]:
    app = str(record.get("application_number") or record.get("publication_number") or "").split(".")[0]
    publication_number = str(record.get("publication_number") or "")
    started = datetime.now(timezone.utc).isoformat()
    status = "ok"
    error = ""
    pdf_path = ""
    artifact_path = ""
    url = ""
    formats: set[str] = set()

    try:
        publication, publication_server_id, kind = normalize_publication_number(publication_number)
        case_dir = output_root / app
        target_dir = case_dir / "original-application"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{publication}_publication-server.pdf"
        out_path = target_dir / file_name
        patent_url = f"https://data.epo.org/publication-server/rest/v1.2/patents/{publication_server_id}"
        url = f"{patent_url}/document.pdf"
        pdf_path = str(out_path)

        if skip_existing and is_valid_pdf(out_path):
            status = "skipped"
        else:
            try:
                formats = available_formats(patent_url, timeout)
            except RuntimeError as exc:
                if "HTTP 500" not in str(exc):
                    raise
                status = "publication_unavailable"
                (target_dir / "publication-server-source.json").write_text(
                    json.dumps(
                        {
                            "application_number": app,
                            "publication_number": publication,
                            "publication_server_id": publication_server_id,
                            "patent_url": patent_url,
                            "source": "EPO European Publication Server REST API",
                            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return {
                    "index": str(index),
                    "application_number": app,
                    "publication_number": publication_number,
                    "status": status,
                    "started_at_utc": started,
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                    "pdf_path": "",
                    "artifact_path": "",
                    "formats": "",
                    "url": patent_url,
                    "error": "",
                }
            if "pdf" not in formats:
                selected_format = next((fmt for fmt in fallback_formats if fmt in formats), "")
                if selected_format:
                    fallback_file_name = f"{publication}_publication-server.{selected_format}"
                    fallback_path = target_dir / fallback_file_name
                    fallback_url = f"{patent_url}/document.{selected_format}"
                    if not (skip_existing and is_existing_artifact(fallback_path)):
                        download_artifact(fallback_url, fallback_path, timeout)
                    artifact_path = str(fallback_path)

                    index_path = target_dir / "download-index.csv"
                    rows = [
                        row
                        for row in existing_download_index_rows(index_path)
                        if row.get("documentId") != publication_server_id
                    ]
                    rows.append(
                        {
                            "applicationNumber": app,
                            "documentId": publication_server_id,
                            "date": "",
                            "title": f"European publication {publication}",
                            "phase": f"Publication Server {kind} ({selected_format})",
                            "pages": "",
                            "fileName": fallback_file_name,
                            "path": str(fallback_path),
                            "url": fallback_url,
                        }
                    )
                    write_csv(
                        index_path,
                        rows,
                        ["applicationNumber", "documentId", "date", "title", "phase", "pages", "fileName", "path", "url"],
                    )
                    status = f"{selected_format}_ok"
                    url = fallback_url
                else:
                    status = "pdf_unavailable"
                (target_dir / "publication-server-source.json").write_text(
                    json.dumps(
                        {
                            "application_number": app,
                            "publication_number": publication,
                            "publication_server_id": publication_server_id,
                            "formats": sorted(formats),
                            "patent_url": patent_url,
                            "artifact_path": artifact_path,
                            "source": "EPO European Publication Server REST API",
                            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return {
                    "index": str(index),
                    "application_number": app,
                    "publication_number": publication_number,
                    "status": status,
                    "started_at_utc": started,
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                    "pdf_path": "",
                    "artifact_path": artifact_path,
                    "formats": ",".join(sorted(formats)),
                    "url": url or patent_url,
                    "error": "",
                }
            download_pdf(url, out_path, timeout)

        index_path = target_dir / "download-index.csv"
        rows = [
            row
            for row in existing_download_index_rows(index_path)
            if row.get("documentId") != publication_server_id
        ]
        rows.append(
            {
                "applicationNumber": app,
                "documentId": publication_server_id,
                "date": "",
                "title": f"European publication {publication}",
                "phase": f"Publication Server {kind}",
                "pages": "",
                "fileName": file_name,
                "path": str(out_path),
                "url": url,
            }
        )
        write_csv(
            index_path,
            rows,
            ["applicationNumber", "documentId", "date", "title", "phase", "pages", "fileName", "path", "url"],
        )
        (target_dir / "publication-server-source.json").write_text(
            json.dumps(
                {
                    "application_number": app,
                    "publication_number": publication,
                    "publication_server_id": publication_server_id,
                    "url": url,
                    "pdf_path": str(out_path),
                    "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source": "EPO European Publication Server REST API",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        status = "error"
        error = repr(exc)

    return {
        "index": str(index),
        "application_number": app,
        "publication_number": publication_number,
        "status": status,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "pdf_path": pdf_path,
        "artifact_path": artifact_path,
        "formats": ",".join(sorted(formats)),
        "url": url,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch EP A/B publication PDFs from the EPO European Publication Server.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--status-file", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--only-missing-original", action="store_true")
    parser.add_argument(
        "--fallback-formats",
        default="html,xml,zip",
        help="Comma-separated Publication Server formats to download when PDF is unavailable.",
    )
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    output_root = project_root / args.output_root
    manifest = project_root / args.manifest
    status_path = Path(args.status_file) if args.status_file else output_root / "batch-publication-server-fetch-status.csv"
    if not status_path.is_absolute():
        status_path = project_root / status_path

    records = load_records(manifest, args.limit, args.offset)
    fallback_formats = [item.strip().lower() for item in args.fallback_formats.split(",") if item.strip()]
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records, start=args.offset + 1):
        app = str(record.get("application_number") or "").split(".")[0]
        if args.only_missing_original and (output_root / app / "original-application" / "download-index.csv").exists():
            continue
        row = fetch_record(index, record, output_root, args.timeout, args.skip_existing, fallback_formats)
        rows.append(row)
        write_csv(status_path, rows, STATUS_FIELDS)
        print(f"[done] {row['application_number']}: {row['status']} {row['publication_number']}", flush=True)
        if args.delay_seconds > 0:
            time.sleep(args.delay_seconds)

    write_csv(status_path, rows, STATUS_FIELDS)
    ok = sum(1 for row in rows if row["status"] in {"ok", "skipped", "pdf_unavailable", "publication_unavailable", "html_ok", "xml_ok", "zip_ok"})
    failed = sum(1 for row in rows if row["status"] == "error")
    print(f"Wrote status: {status_path}")
    print(f"non_error={ok} error={failed}")


if __name__ == "__main__":
    main()

