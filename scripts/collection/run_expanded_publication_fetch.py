from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run(args: list[str], cwd: Path) -> None:
    print("[run]", " ".join(args), flush=True)
    subprocess.run(args, cwd=str(cwd), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand EP candidate pool and fetch official publication artifacts.")
    parser.add_argument("--candidate-output", default="markush-run/benchmark/ep_application_candidates_expanded.json")
    parser.add_argument("--existing-pool", action="append", default=["markush-run/benchmark/ep_review_file_sources_verified500_keywords.json"])
    parser.add_argument("--output-root", default="markush-run/benchmark-target500")
    parser.add_argument("--ledger-output", default="markush-run/benchmark-target500/_state/collection-ledger.json")
    parser.add_argument("--candidate-limit", type=int, default=1000)
    parser.add_argument("--pages-per-query", type=int, default=4)
    parser.add_argument("--max-detail", type=int, default=3500)
    parser.add_argument("--detail-workers", type=int, default=8)
    parser.add_argument("--detail-delay", type=float, default=0.05)
    parser.add_argument("--skip-candidate-collection", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--fallback-formats", default="html,xml,zip")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    py = sys.executable
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate_output = args.candidate_output
    fetch_status = f"{args.output_root}/_state/expanded-publication-fetch-status-{stamp}.csv"

    if not args.skip_candidate_collection:
        run(
            [
                py,
                "scripts/collect_ep_application_candidates.py",
                "-o",
                candidate_output,
                "--limit",
                str(args.candidate_limit),
                "--pages-per-query",
                str(args.pages_per_query),
                "--max-detail",
                str(args.max_detail),
                "--detail-workers",
                str(args.detail_workers),
                "--detail-delay",
                str(args.detail_delay),
            ],
            project_root,
        )

    run(
        [
            py,
            "scripts/fetch_publication_server_pdfs.py",
            "--manifest",
            candidate_output,
            "--output-root",
            args.output_root,
            "--status-file",
            fetch_status,
            "--skip-existing",
            "--only-missing-original",
            "--delay-seconds",
            str(args.delay_seconds),
            "--fallback-formats",
            args.fallback_formats,
        ],
        project_root,
    )

    ledger_args = [
        py,
        "scripts/update_collection_ledger.py",
        "--output-root",
        args.output_root,
        "--output",
        args.ledger_output,
    ]
    for pool in args.existing_pool:
        ledger_args += ["--pool", pool]
    ledger_args += ["--pool", candidate_output]
    run(ledger_args, project_root)


if __name__ == "__main__":
    main()

