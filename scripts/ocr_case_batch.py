from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


PAGE_MARKER_PATTERN = re.compile(r"---\s*PAGE\s+[0-9]+\s*---", re.IGNORECASE)


def meaningful_char_count(text: str) -> int:
    cleaned = PAGE_MARKER_PATTERN.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned))


def valid_text_for(pdf: Path) -> bool:
    out = pdf.with_name(pdf.stem + "_ocr.txt")
    if not out.exists() or out.stat().st_size <= 80:
        return False
    return meaningful_char_count(out.read_text(encoding="utf-8", errors="ignore")) > 80


def discover_case_dirs(root: Path) -> list[Path]:
    if root.name.startswith("EP") and ((root / "docs").exists() or (root / "original-application").exists()):
        return [root]
    case_dirs = [
        path
        for path in root.rglob("EP*")
        if path.is_dir() and ((path / "docs").exists() or (path / "original-application").exists())
    ]
    return sorted(case_dirs)


def run_one(pdf: Path, script: Path, zoom: float, timeout: int, overwrite: bool) -> dict[str, str]:
    out = pdf.with_name(pdf.stem + "_ocr.txt")
    if valid_text_for(pdf) and not overwrite:
        return {
            "pdf": str(pdf),
            "txt": str(out),
            "status": "cached",
            "returncode": "0",
            "error": "",
        }

    cmd = [
        sys.executable,
        str(script),
        str(pdf),
        "--zoom",
        str(zoom),
    ]
    if overwrite:
        cmd.append("--overwrite")

    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "pdf": str(pdf),
            "txt": str(out),
            "status": "timeout",
            "returncode": "",
            "error": f"timeout after {timeout}s",
        }

    ok = result.returncode == 0 and valid_text_for(pdf)
    return {
        "pdf": str(pdf),
        "txt": str(out),
        "status": "ok" if ok else "error",
        "returncode": str(result.returncode),
        "error": (result.stderr or result.stdout or "")[-1200:].replace("\r", " ").replace("\n", " "),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OCR per PDF with concurrency and per-file timeout.")
    parser.add_argument("root", help="Benchmark root or case directory.")
    parser.add_argument("--scope", choices=["docs", "original", "both"], default="docs")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--zoom", type=float, default=1.6)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--include-regex", default="")
    parser.add_argument("--exclude-regex", default="")
    parser.add_argument("--status", default="")
    args = parser.parse_args()

    root = Path(args.root)
    script = Path(__file__).with_name("ocr-pdfs.py")
    pdfs: list[Path] = []
    scopes = ["docs", "original-application"] if args.scope == "both" else [args.scope]
    if args.scope == "original":
        scopes = ["original-application"]

    case_dirs = discover_case_dirs(root)

    for case_dir in case_dirs:
        for scope in scopes:
            directory = case_dir / scope
            if directory.exists():
                pdfs.extend(sorted(directory.glob("*.pdf")))

    if not args.overwrite:
        pdfs = [pdf for pdf in pdfs if not valid_text_for(pdf)]
    if args.include_regex:
        include_re = re.compile(args.include_regex, re.I)
        pdfs = [pdf for pdf in pdfs if include_re.search(pdf.name)]
    if args.exclude_regex:
        exclude_re = re.compile(args.exclude_regex, re.I)
        pdfs = [pdf for pdf in pdfs if not exclude_re.search(pdf.name)]

    status_path = Path(args.status) if args.status else root / f"_ocr_{args.scope}_status.csv"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["pdf", "txt", "status", "returncode", "error"]

    print(f"OCR targets: {len(pdfs)}; workers={args.workers}; timeout={args.timeout}s; status={status_path}", flush=True)
    rows: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run_one, pdf, script, args.zoom, args.timeout, args.overwrite) for pdf in pdfs]
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            rows.append(row)
            print(f"[{index}/{len(futures)}] {row['status']} {row['pdf']}", flush=True)
            if index % 10 == 0 or index == len(futures):
                with status_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)

    with status_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    failed = [row for row in rows if row["status"] not in {"ok", "cached"}]
    print(f"Done. ok_or_cached={len(rows)-len(failed)} failed={len(failed)} status={status_path}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
