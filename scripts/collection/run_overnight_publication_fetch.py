from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_id(value: Any) -> str:
    text = str(value or "").strip().upper().split(".")[0]
    if text and not text.startswith("EP"):
        text = f"EP{text}"
    return text


def publication_key(value: Any) -> str:
    return str(value or "").strip().upper()


def run_step(args: list[str], cwd: Path, deadline: datetime) -> bool:
    if datetime.now(timezone.utc) >= deadline:
        return False
    print("[run]", " ".join(args), flush=True)
    proc = subprocess.Popen(args, cwd=str(cwd))
    while proc.poll() is None:
        if datetime.now(timezone.utc) >= deadline:
            print(f"[timeout] stopping pid={proc.pid}", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return False
        time.sleep(5)
    if proc.returncode != 0:
        print(f"[warn] command exited with {proc.returncode}", flush=True)
        return False
    return True


def load_pool_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            data = read_json(path)
            records.extend(data.get("records", []))
    return records


def merge_records(paths: list[Path]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_publication: dict[str, str] = {}
    for record in load_pool_records(paths):
        app = normalize_id(record.get("application_number") or record.get("publication_number"))
        publication = publication_key(record.get("publication_number"))
        if not app:
            continue
        key = by_publication.get(publication) if publication else ""
        if not key:
            key = app
            if publication:
                by_publication[publication] = key
        existing = by_id.get(key)
        if existing is None:
            copied = dict(record)
            copied["application_number"] = app
            if publication:
                copied["publication_number"] = publication
            by_id[key] = copied
            continue
        related = existing.setdefault("related_publication_numbers", [])
        if publication and publication != existing.get("publication_number") and publication not in related:
            related.append(publication)
        if not existing.get("publication_number") and publication:
            existing["publication_number"] = publication
        if normalize_id(existing.get("application_number", "")).endswith(("A1", "A2", "A3", "A4", "B1", "B2")):
            existing["application_number"] = app
        for field in ("title", "keyword_group", "category", "benchmark_label", "google_patents_url"):
            if not existing.get(field) and record.get(field):
                existing[field] = record[field]
    return sorted(by_id.values(), key=lambda row: (row.get("keyword_group", ""), row.get("publication_number", "")))


def discovered_publications(output_root: Path) -> set[str]:
    seen: set[str] = set()
    for path in output_root.glob("EP*/original-application/*_publication-server.*"):
        name = path.name.split("_publication-server.", 1)[0].upper()
        if name.startswith("EP"):
            seen.add(name)
    for path in output_root.glob("EP*/original-application/publication-server-source.json"):
        try:
            source = read_json(path)
        except Exception:
            continue
        publication = publication_key(source.get("publication_number"))
        if publication:
            seen.add(publication)
    return seen


def filter_missing_publications(records: list[dict[str, Any]], output_root: Path) -> list[dict[str, Any]]:
    seen = discovered_publications(output_root)
    missing: list[dict[str, Any]] = []
    for record in records:
        publication = publication_key(record.get("publication_number"))
        if publication and publication in seen:
            continue
        missing.append(record)
    return missing


def status_counts(paths: list[Path]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in paths:
        for row in read_csv(path):
            counts[row.get("status", "")] += 1
    return dict(counts)


def write_manifest(path: Path, records: list[dict[str, Any]], source_files: list[Path], metadata: dict[str, Any]) -> None:
    write_json(
        path,
        {
            "metadata": {
                **metadata,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_files": [str(item) for item in source_files],
            },
            "stats": {
                "total_records": len(records),
                "benchmark_labels": dict(Counter(str(row.get("benchmark_label") or "") for row in records)),
                "keyword_groups": dict(Counter(str(row.get("keyword_group") or "") for row in records)),
            },
            "records": records,
        },
    )


def fetch_missing_and_update_ledger(
    *,
    py: str,
    project_root: Path,
    output_root_arg: str,
    ledger_output_arg: str,
    fallback_formats: str,
    delay_seconds: float,
    pool_paths: list[Path],
    cumulative_path: Path,
    missing_path: Path,
    fetch_status: Path,
    deadline: datetime,
    description: str,
) -> dict[str, Any]:
    output_root = project_root / output_root_arg
    merged_records = merge_records(pool_paths)
    missing_records = filter_missing_publications(merged_records, output_root)
    write_manifest(
        cumulative_path,
        merged_records,
        pool_paths,
        {"description": "Cumulative overnight candidate pool for Publication Server-only fetching."},
    )
    write_manifest(missing_path, missing_records, pool_paths, {"description": description})

    if missing_records:
        run_step(
            [
                py,
                "scripts/fetch_publication_server_pdfs.py",
                "--manifest",
                str(missing_path.relative_to(project_root)),
                "--output-root",
                output_root_arg,
                "--status-file",
                str(fetch_status.relative_to(project_root)),
                "--skip-existing",
                "--only-missing-original",
                "--delay-seconds",
                str(delay_seconds),
                "--fallback-formats",
                fallback_formats,
            ],
            project_root,
            deadline,
        )
    else:
        print("[info] no missing publication artifacts to fetch", flush=True)

    ledger_args = [
        py,
        "scripts/update_collection_ledger.py",
        "--output-root",
        output_root_arg,
        "--output",
        ledger_output_arg,
    ]
    for pool in pool_paths + [cumulative_path]:
        ledger_args += ["--pool", str(pool.relative_to(project_root))]
    run_step(ledger_args, project_root, deadline)

    return {
        "cumulative_file": str(cumulative_path),
        "missing_file": str(missing_path),
        "fetch_status": str(fetch_status) if fetch_status.exists() else "",
        "merged_records": len(merged_records),
        "missing_records_at_phase_start": len(missing_records),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a time-bounded Publication Server-only overnight fetch loop.")
    parser.add_argument("--hours", type=float, default=10.0)
    parser.add_argument("--output-root", default="markush-run/benchmark-target500")
    parser.add_argument("--work-dir", default="markush-run/benchmark")
    parser.add_argument("--base-pool", action="append", default=[])
    parser.add_argument("--initial-pages-per-query", type=int, default=4)
    parser.add_argument("--max-pages-per-query", type=int, default=12)
    parser.add_argument("--limit", type=int, default=2200)
    parser.add_argument("--max-detail", type=int, default=3200)
    parser.add_argument("--collect-timeout-minutes", type=float, default=45.0)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--fallback-formats", default="html,xml,zip")
    parser.add_argument("--ledger-output", default="markush-run/benchmark-target500/_state/collection-ledger.json")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    py = sys.executable
    output_root = project_root / args.output_root
    state_dir = output_root / "_state"
    work_dir = project_root / args.work_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    deadline = datetime.now(timezone.utc) + timedelta(hours=args.hours)
    summary_path = state_dir / f"overnight-publication-fetch-summary-{stamp}.json"
    cumulative_path = work_dir / f"ep_application_candidates_overnight_{stamp}_merged.json"
    missing_path = work_dir / f"ep_application_candidates_overnight_{stamp}_missing.json"

    base_pools = [project_root / item for item in args.base_pool]
    candidate_paths: list[Path] = []
    fetch_status_paths: list[Path] = []
    rounds: list[dict[str, Any]] = []

    print(f"[start] deadline_utc={deadline.isoformat()}", flush=True)
    pages = args.initial_pages_per_query
    round_number = 1
    while datetime.now(timezone.utc) < deadline:
        candidate_path = work_dir / f"ep_application_candidates_overnight_{stamp}_round{round_number:02d}.json"
        pre_fetch_status = state_dir / f"overnight-publication-fetch-status-{stamp}-round{round_number:02d}-pre.csv"
        post_fetch_status = state_dir / f"overnight-publication-fetch-status-{stamp}-round{round_number:02d}-post.csv"
        pages = min(args.max_pages_per_query, pages)

        all_pool_paths = base_pools + candidate_paths
        pre_info = fetch_missing_and_update_ledger(
            py=py,
            project_root=project_root,
            output_root_arg=args.output_root,
            ledger_output_arg=args.ledger_output,
            fallback_formats=args.fallback_formats,
            delay_seconds=args.delay_seconds,
            pool_paths=all_pool_paths,
            cumulative_path=cumulative_path,
            missing_path=missing_path,
            fetch_status=pre_fetch_status,
            deadline=deadline,
            description="Overnight candidates without an observed Publication Server artifact before candidate expansion.",
        )
        if pre_fetch_status.exists():
            fetch_status_paths.append(pre_fetch_status)

        collect_deadline = min(deadline, datetime.now(timezone.utc) + timedelta(minutes=args.collect_timeout_minutes))
        collected = run_step(
            [
                py,
                "scripts/collect_ep_application_candidates.py",
                "-o",
                str(candidate_path.relative_to(project_root)),
                "--limit",
                str(args.limit),
                "--pages-per-query",
                str(pages),
                "--max-detail",
                str(args.max_detail),
                "--skip-detail",
            ],
            project_root,
            collect_deadline,
        )
        if collected and candidate_path.exists():
            candidate_paths.append(candidate_path)

        all_pool_paths = base_pools + candidate_paths
        post_info = fetch_missing_and_update_ledger(
            py=py,
            project_root=project_root,
            output_root_arg=args.output_root,
            ledger_output_arg=args.ledger_output,
            fallback_formats=args.fallback_formats,
            delay_seconds=args.delay_seconds,
            pool_paths=all_pool_paths,
            cumulative_path=cumulative_path,
            missing_path=missing_path,
            fetch_status=post_fetch_status,
            deadline=deadline,
            description="Overnight candidates without an observed Publication Server artifact after candidate expansion.",
        )
        if post_fetch_status.exists():
            fetch_status_paths.append(post_fetch_status)

        round_info = {
            "round": round_number,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "pages_per_query": pages,
            "candidate_file": str(candidate_path),
            "cumulative_file": str(cumulative_path),
            "missing_file": str(missing_path),
            "pre_fetch": pre_info,
            "post_fetch": post_info,
            "status_counts_all_overnight": status_counts(fetch_status_paths),
        }
        rounds.append(round_info)
        write_json(
            summary_path,
            {
                "started_at_utc": stamp,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "deadline_utc": deadline.isoformat(),
                "cumulative_pool": str(cumulative_path),
                "missing_pool": str(missing_path),
                "ledger": str(project_root / args.ledger_output),
                "rounds": rounds,
            },
        )

        round_number += 1
        pages = min(args.max_pages_per_query, pages + 2)
        if pages >= args.max_pages_per_query and not post_info["missing_records_at_phase_start"]:
            time.sleep(min(900, max(1, int((deadline - datetime.now(timezone.utc)).total_seconds()))))
        else:
            time.sleep(min(120, max(1, int((deadline - datetime.now(timezone.utc)).total_seconds()))))

    print(f"[done] summary={summary_path}", flush=True)


if __name__ == "__main__":
    main()

