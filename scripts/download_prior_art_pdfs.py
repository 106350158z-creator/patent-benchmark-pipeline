from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

from build_benchmark_input import normalize_patent_publication, wipo_doc_id


USER_AGENT = "Mozilla/5.0 (compatible; epo-report-analysis/1.0)"
PDF_META_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
PDF_HREF_RE = re.compile(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.I)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_bytes(url: str, timeout: int) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def request_text(url: str, timeout: int) -> str:
    return request_bytes(url, timeout).decode("utf-8", errors="replace")


def google_publication_candidates(publication: str) -> list[str]:
    candidates: list[str] = []
    for candidate in [publication, wipo_doc_id(publication)]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    base = re.sub(r"[A-Z]\d?$", "", publication)
    if base and base not in candidates:
        candidates.append(base)
    return candidates


def find_pdf_url(publication: str, timeout: int) -> tuple[str, str, str]:
    for candidate in google_publication_candidates(publication):
        page_url = f"https://patents.google.com/patent/{candidate}/en"
        try:
            html = request_text(page_url, timeout)
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        urls = PDF_META_RE.findall(html) or PDF_HREF_RE.findall(html)
        for raw_url in urls:
            pdf_url = unquote(raw_url.replace("&amp;", "&"))
            if pdf_url.lower().endswith(".pdf") or ".pdf?" in pdf_url.lower():
                return pdf_url, page_url, candidate
    return "", "", ""


def relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def valid_existing_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"%PDF"
    except OSError:
        return False


def update_prior_art_item(
    item: dict[str, Any],
    case_dir: Path,
    timeout: int,
    sleep_seconds: float,
    force: bool,
) -> dict[str, Any]:
    citation = str(item.get("citation") or "")
    publication = normalize_patent_publication(citation)
    item["publication_number"] = publication
    if not publication:
        item["pdf_download_status"] = "skipped_unverified_publication"
        return item

    output_dir = case_dir / "prior-art"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{publication}.pdf"
    if output_path.exists() and not force and valid_existing_pdf(output_path):
        data = output_path.read_bytes()
        item["local_pdf"] = relative(output_path, case_dir)
        item["pdf_sha256"] = sha256_bytes(data)
        item["pdf_bytes"] = len(data)
        item["pdf_download_status"] = "existing"
        return item

    pdf_url, page_url, resolved_publication = find_pdf_url(publication, timeout)
    item["pdf_lookup_url"] = page_url
    item["pdf_lookup_publication"] = resolved_publication
    if not pdf_url:
        item.pop("local_pdf", None)
        item["pdf_download_status"] = "missing_pdf_url"
        return item

    item["pdf_download_url"] = pdf_url
    try:
        data = request_bytes(pdf_url, timeout)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        item.pop("local_pdf", None)
        item["pdf_download_status"] = f"download_error: {exc}"
        return item

    if not data.startswith(b"%PDF"):
        item.pop("local_pdf", None)
        item["pdf_download_status"] = "downloaded_non_pdf"
        return item

    output_path.write_bytes(data)
    item["local_pdf"] = relative(output_path, case_dir)
    item["pdf_sha256"] = sha256_bytes(data)
    item["pdf_bytes"] = len(data)
    item["pdf_downloaded_at_utc"] = datetime.now(timezone.utc).isoformat()
    item["pdf_download_status"] = "downloaded"
    if sleep_seconds:
        time.sleep(sleep_seconds)
    return item


def iter_benchmark_inputs(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("*-benchmark-input.json"))


def update_file(path: Path, timeout: int, sleep_seconds: float, force: bool) -> tuple[int, int, int]:
    data = read_json(path)
    docs = ((data.get("benchmark_input") or {}).get("prior_art_docs") or [])
    case_dir = path.parent
    total = downloaded = failed = 0
    for item in docs:
        if not isinstance(item, dict):
            continue
        total += 1
        before = item.get("pdf_download_status")
        update_prior_art_item(item, case_dir, timeout, sleep_seconds, force)
        status = str(item.get("pdf_download_status") or "")
        if status in {"downloaded", "existing"}:
            downloaded += 1
        elif status != before:
            failed += 1
    write_json(path, data)
    return total, downloaded, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Download prior-art patent PDFs and write local paths into benchmark input JSON.")
    parser.add_argument("root", help="Benchmark input JSON, case directory, or nested root containing benchmark inputs.")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--force", action="store_true", help="Re-download even when a valid local PDF already exists.")
    args = parser.parse_args()

    root = Path(args.root)
    paths = iter_benchmark_inputs(root)
    if not paths:
        print(f"No benchmark input JSON files found under {root}", file=sys.stderr)
        raise SystemExit(1)

    overall = [0, 0, 0]
    for path in paths:
        total, ok, failed = update_file(path, args.timeout, args.sleep_seconds, args.force)
        overall[0] += total
        overall[1] += ok
        overall[2] += failed
        print(f"{path}: prior_art={total}, local_pdf={ok}, unresolved={total - ok}")

    if overall[0] and overall[1] == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
