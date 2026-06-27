import argparse
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_command(args: list[str], cwd: Path, timeout: int | None = None) -> None:
    print("[run]", " ".join(args), flush=True)
    subprocess.run(args, cwd=str(cwd), check=True, timeout=timeout)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


STATUS_FIELDS = [
    "index",
    "application_number",
    "publication_number",
    "benchmark_label",
    "keyword_group",
    "stage",
    "analysis_mode",
    "status",
    "started_at_utc",
    "finished_at_utc",
    "benchmark_input",
    "analysis_json",
    "analysis_html",
    "error",
]


def make_slim_benchmark(full_path: Path) -> Path:
    data = read_json(full_path)
    bench = data.get("benchmark_input") or {}

    structure = bench.get("drug_structure") or {}
    structure.pop("markush_candidate_images", None)
    structure.pop("markush_page_images", None)
    if isinstance(structure.get("markush_or_formula_snippets"), list):
        structure["markush_or_formula_snippets"] = structure["markush_or_formula_snippets"][:3]
    bench["drug_structure"] = structure

    claim = bench.get("claim_text") or {}
    claim.pop("raw_extracted_text", None)
    if isinstance(claim.get("llm_claim_extraction"), dict):
        claim["llm_claim_extraction"] = {
            key: value
            for key, value in claim["llm_claim_extraction"].items()
            if key in {"claim_number", "source", "extraction_note"}
        }
    bench["claim_text"] = claim

    spec = bench.get("specification_data") or {}
    for key, value in list(spec.items()):
        if isinstance(value, list):
            spec[key] = value[:3]
    bench["specification_data"] = spec
    bench["prior_art_docs"] = (bench.get("prior_art_docs") or [])[:10]
    data["benchmark_input"] = bench

    slim_path = full_path.with_name(full_path.stem + ".slim.json")
    write_json(slim_path, data)
    return slim_path


def load_records(manifest: Path, limit: int, offset: int) -> list[dict[str, Any]]:
    data = read_json(manifest)
    records = [record for record in data.get("records", []) if record.get("application_number")]
    return records[offset : offset + limit if limit else None]


def write_status(status_path: Path, rows: list[dict[str, str]]) -> None:
    sorted_rows = sorted(rows, key=lambda row: int(row.get("index") or 0))
    with status_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_rows)


def run_collect(app: str, args: argparse.Namespace, project_root: Path) -> None:
    collect_args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "scripts\\run_epo_benchmark.ps1",
        "-ApplicationNumber",
        app,
        "-OutputRoot",
        args.output_root,
        "-SkipRefine",
        "-ContinueOnDownloadError",
        "-EnvFile",
        args.env_file,
        "-ApiKeyEnv",
        args.api_key_env,
        "-BaseUrl",
        args.base_url,
        "-Model",
        args.model,
        "-TopK",
        str(args.top_k),
    ]
    if args.run_ocr:
        collect_args.append("-RunOcr")
        if args.ocr_scope:
            collect_args += ["-OcrScope", args.ocr_scope]
        if args.ocr_include_regex:
            collect_args += ["-OcrIncludeRegex", args.ocr_include_regex]
        if args.ocr_exclude_regex:
            collect_args += ["-OcrExcludeRegex", args.ocr_exclude_regex]
        collect_args += [
            "-OcrWorkers",
            str(args.ocr_workers),
            "-OcrTimeoutSeconds",
            str(args.ocr_timeout_seconds),
            "-OcrZoom",
            str(args.ocr_zoom),
        ]
    if args.extract_pdf_text:
        collect_args.append("-ExtractPdfText")
    run_command(collect_args, cwd=project_root)


def run_refine(app: str, benchmark_input: Path, args: argparse.Namespace, project_root: Path) -> None:
    refine_args = [
        sys.executable,
        "scripts\\refine_benchmark_preview.py",
        str(benchmark_input),
        "--env-file",
        args.env_file,
        "--api-key-env",
        args.api_key_env,
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--temperature",
        "0",
    ]
    print(f"[refine] {app}", flush=True)
    run_command(refine_args, cwd=project_root)


def rebuild_benchmark_input(app: str, case_dir: Path, benchmark_input: Path, args: argparse.Namespace, project_root: Path) -> None:
    build_args = [
        sys.executable,
        "scripts\\build_benchmark_input.py",
        str(case_dir),
        "--application-number",
        app,
        "--top-k",
        str(args.top_k),
        "-o",
        str(benchmark_input),
    ]
    print(f"[refresh-input] {app}", flush=True)
    run_command(build_args, cwd=project_root)


def run_analysis(app: str, benchmark_input: Path, analysis_json: Path, analysis_html: Path, args: argparse.Namespace, project_root: Path) -> None:
    run_refine(app, benchmark_input, args, project_root)

    if args.analysis_mode == "split":
        analysis_args = [
            sys.executable,
            "scripts\\generate_analysis_json_split.py",
            str(benchmark_input),
            "-o",
            str(analysis_json),
            "--env-file",
            args.env_file,
            "--api-key-env",
            args.api_key_env,
            "--base-url",
            args.base_url,
            "--model",
            args.model,
            "--temperature",
            "0",
            "--max-source-files",
            str(args.max_source_files),
            "--max-chars-per-file",
            str(args.max_chars_per_file),
            "--max-field-chars",
            str(args.max_field_chars),
            "--max-prior-art",
            str(args.max_prior_art),
            "--max-tokens",
            str(args.max_tokens),
            "--meta-max-tokens",
            str(args.meta_max_tokens),
            "--request-timeout",
            str(args.request_timeout),
            "--reasoning-effort",
            args.reasoning_effort,
            "--verbosity",
            args.verbosity,
        ]
        if args.write_analysis_steps:
            analysis_args.append("--write-steps")
    else:
        slim_input = make_slim_benchmark(benchmark_input)
        analysis_args = [
            sys.executable,
            "scripts\\generate_analysis_json.py",
            str(slim_input),
            "-o",
            str(analysis_json),
            "--env-file",
            args.env_file,
            "--api-key-env",
            args.api_key_env,
            "--base-url",
            args.base_url,
            "--model",
            args.model,
            "--temperature",
            "0",
            "--max-source-files",
            str(args.max_source_files),
            "--max-chars-per-file",
            str(args.max_chars_per_file),
            "--max-tokens",
            str(args.max_tokens),
            "--request-timeout",
            str(args.request_timeout),
            "--reasoning-effort=",
            "--verbosity=",
        ]
    run_command(analysis_args, cwd=project_root)
    run_command(
        [
            sys.executable,
            "scripts\\translate_report_lists.py",
            str(analysis_json),
            "--env-file",
            args.env_file,
            "--api-key-env",
            args.api_key_env,
            "--base-url",
            args.base_url,
            "--model",
            args.model,
            "--temperature",
            "0",
        ],
        cwd=project_root,
    )
    run_command(
        [
            sys.executable,
            "scripts\\download_prior_art_pdfs.py",
            str(benchmark_input),
        ],
        cwd=project_root,
    )
    run_command(
        [
            sys.executable,
            "scripts\\json_to_html_report.py",
            str(analysis_json),
            "-o",
            str(analysis_html),
            "--benchmark-input",
            str(benchmark_input),
        ],
        cwd=project_root,
    )


def run_record(index: int, record: dict[str, Any], args: argparse.Namespace, project_root: Path, output_root: Path) -> dict[str, str]:
    app = str(record["application_number"]).split(".")[0]
    case_dir = output_root / app
    benchmark_input = case_dir / f"{app}-benchmark-input.json"
    analysis_json = case_dir / f"{app}-analysis.json"
    analysis_html = case_dir / f"{app}-analysis.html"
    started = datetime.now(timezone.utc).isoformat()
    status = "ok"
    error = ""

    try:
        if args.stage in {"collect", "all"}:
            if args.skip_existing and benchmark_input.exists():
                print(f"[skip] {app} already has benchmark input", flush=True)
            else:
                run_collect(app, args, project_root)

        if args.stage in {"analysis", "all"}:
            refreshed_input = False
            if not benchmark_input.exists():
                if args.refresh_input_before_analysis and case_dir.exists():
                    rebuild_benchmark_input(app, case_dir, benchmark_input, args, project_root)
                    refreshed_input = True
                else:
                    raise RuntimeError(f"Benchmark input not found for {app}: {benchmark_input}")
            if args.skip_existing and analysis_json.exists() and analysis_html.exists():
                print(f"[skip] {app} already has analysis JSON and HTML", flush=True)
            else:
                if args.refresh_input_before_analysis and not refreshed_input:
                    rebuild_benchmark_input(app, case_dir, benchmark_input, args, project_root)
                run_analysis(app, benchmark_input, analysis_json, analysis_html, args, project_root)
    except Exception as exc:
        status = "error"
        error = repr(exc)
        print(f"[error] {app}: {error}", flush=True)

    return {
        "index": str(index),
        "application_number": app,
        "publication_number": str(record.get("publication_number") or ""),
        "benchmark_label": str(record.get("benchmark_label") or ""),
        "keyword_group": str(record.get("keyword_group") or ""),
        "stage": args.stage,
        "analysis_mode": args.analysis_mode,
        "status": status,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark_input": str(benchmark_input),
        "analysis_json": str(analysis_json),
        "analysis_html": str(analysis_html),
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the existing EPO benchmark pipeline for records in a manifest.")
    parser.add_argument("--manifest", default="markush-run/benchmark/ep_review_file_sources_merged_current.json")
    parser.add_argument("--output-root", default="markush-run/benchmark-api50")
    parser.add_argument("--stage", choices=["collect", "analysis", "all"], default="all")
    parser.add_argument("--status-file", default="", help="Status CSV filename under output root. Defaults to batch-<stage>-status.csv.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://yunwu.ai/v1")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--analysis-mode", choices=["single", "split"], default="split")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-source-files", type=int, default=6)
    parser.add_argument("--max-chars-per-file", type=int, default=2600)
    parser.add_argument("--max-field-chars", type=int, default=2200)
    parser.add_argument("--max-prior-art", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--meta-max-tokens", type=int, default=800)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--verbosity", default="low")
    parser.add_argument("--write-analysis-steps", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--run-ocr", action="store_true", help="Run local OCR during collect. By default collect skips OCR.")
    parser.add_argument("--extract-pdf-text", action="store_true", help="Extract embedded PDF text before optional OCR.")
    parser.add_argument("--ocr-scope", choices=["docs", "original", "both"], default="docs")
    parser.add_argument("--ocr-include-regex", default="claims|communication|decision|annex|reply|search_opinion|search_report|amended_claims")
    parser.add_argument("--ocr-exclude-regex", default="translation|description|published_international|text_intended")
    parser.add_argument("--ocr-workers", type=int, default=1)
    parser.add_argument("--ocr-timeout-seconds", type=int, default=1200)
    parser.add_argument("--ocr-zoom", type=float, default=1.6)
    parser.add_argument("--no-refresh-input-before-analysis", dest="refresh_input_before_analysis", action="store_false")
    parser.set_defaults(refresh_input_before_analysis=True)
    parser.add_argument("--workers", type=int, default=1, help="Number of records to process concurrently.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    records = load_records(project_root / args.manifest, args.limit, args.offset)
    output_root = project_root / args.output_root
    status_name = args.status_file or ("batch-status.csv" if args.stage == "all" else f"batch-{args.stage}-status.csv")
    status_path = output_root / status_name
    output_root.mkdir(parents=True, exist_ok=True)

    rows_by_app: dict[str, dict[str, str]] = {}
    if status_path.exists():
        with status_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                app = str(row.get("application_number") or "")
                if app:
                    rows_by_app[app] = row

    workers = max(1, args.workers)
    indexed_records = list(enumerate(records, start=args.offset + 1))
    print(f"Running {len(indexed_records)} records with {workers} worker(s). Status: {status_path}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_record, index, record, args, project_root, output_root): (
                index,
                str(record["application_number"]).split(".")[0],
            )
            for index, record in indexed_records
        }
        for future in as_completed(futures):
            index, app = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "index": str(index),
                    "application_number": app,
                    "publication_number": "",
                    "benchmark_label": "",
                    "keyword_group": "",
                    "stage": args.stage,
                    "analysis_mode": args.analysis_mode,
                    "status": "error",
                    "started_at_utc": "",
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                    "benchmark_input": "",
                    "analysis_json": "",
                    "analysis_html": "",
                    "error": repr(exc),
                }
            rows_by_app[app] = row
            write_status(status_path, list(rows_by_app.values()))
            print(f"[done] {app}: {row['status']}", flush=True)

    print(f"Wrote status: {status_path}")


if __name__ == "__main__":
    main()
