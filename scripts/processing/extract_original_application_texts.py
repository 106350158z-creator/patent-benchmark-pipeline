from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


STATUS_FIELDS = [
    "application_number",
    "pdf_count",
    "txt_count",
    "status",
    "started_at_utc",
    "finished_at_utc",
    "error",
]


def case_dirs(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("EP*") if path.is_dir() and (path / "original-application").exists())


def write_status(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract text from original-application PDFs without network access.")
    parser.add_argument("root")
    parser.add_argument("--status-file", default="")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    root = Path(args.root)
    status_path = Path(args.status_file) if args.status_file else root / "_state" / "original-text-extract-status.csv"
    if not status_path.is_absolute():
        status_path = project_root / status_path

    rows: list[dict[str, str]] = []
    for case_dir in case_dirs(root):
        app = case_dir.name
        folder = case_dir / "original-application"
        pdfs = sorted(folder.rglob("*.pdf"))
        if not pdfs:
            continue
        started = datetime.now(timezone.utc).isoformat()
        status = "ok"
        error = ""
        try:
            command = [sys.executable, "scripts/extract_pdf_text.py", str(folder)]
            if args.overwrite:
                command.append("--overwrite")
            subprocess.run(command, cwd=project_root, check=True)
        except Exception as exc:
            status = "error"
            error = repr(exc)
        txts = sorted(folder.rglob("*.txt"))
        row = {
            "application_number": app,
            "pdf_count": str(len(pdfs)),
            "txt_count": str(len(txts)),
            "status": status,
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }
        rows.append(row)
        write_status(status_path, rows)
        print(f"[done] {app}: {status} pdf={len(pdfs)} txt={len(txts)}", flush=True)

    write_status(status_path, rows)
    print(f"Wrote status: {status_path}")


if __name__ == "__main__":
    main()

