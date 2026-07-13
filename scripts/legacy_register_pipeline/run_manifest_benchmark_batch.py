import argparse
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
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


def valid_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5).startswith(b"%PDF-")
    except OSError:
        return False


def resolve_index_pdf(case_dir: Path, folder: Path, row: dict[str, str]) -> Path:
    raw_path = row.get("path") or ""
    if raw_path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        for candidate in (case_dir / path, folder / path):
            if candidate.exists():
                return candidate
    return folder / str(row.get("fileName") or "")


def valid_index_pdf_count(case_dir: Path, folder: Path) -> int:
    rows = read_download_index(folder / "download-index.csv")
    indexed_valid = sum(1 for row in rows if valid_pdf(resolve_index_pdf(case_dir, folder, row)))
    if indexed_valid:
        return indexed_valid
    return sum(1 for path in folder.rglob("*.pdf") if valid_pdf(path))


def fetch_artifacts_exist(case_dir: Path) -> bool:
    return any(
        valid_index_pdf_count(case_dir, case_dir / folder_name) > 0
        for folder_name in ("docs", "original-application")
    )


def raw_complete_artifacts_exist(case_dir: Path) -> bool:
    app = case_dir.name
    register = case_dir / "register"
    register_ok = all(
        path.exists() and path.stat().st_size > 0
        for path in (
            register / f"{app}-main.html",
            register / f"{app}-doclist.html",
            register / f"{app}-doclist.csv",
        )
    )
    docs_ok = (case_dir / "docs" / "download-index.csv").exists() and valid_index_pdf_count(case_dir, case_dir / "docs") > 0
    original_ok = (
        (case_dir / "original-application" / "download-index.csv").exists()
        and valid_index_pdf_count(case_dir, case_dir / "original-application") > 0
    )
    return register_ok and docs_ok and original_ok


def doclist_complete_artifacts_exist(case_dir: Path) -> bool:
    app = case_dir.name
    register = case_dir / "register"
    doclist = register / f"{app}-doclist.csv"
    doclist_ok = doclist.exists() and doclist.stat().st_size > 0
    docs_ok = (case_dir / "docs" / "download-index.csv").exists() and valid_index_pdf_count(case_dir, case_dir / "docs") > 0
    original_ok = (
        (case_dir / "original-application" / "download-index.csv").exists()
        and valid_index_pdf_count(case_dir, case_dir / "original-application") > 0
    )
    return doclist_ok and docs_ok and original_ok


def global_fetch_blocked(error: str) -> bool:
    text = error.lower()
    return any(
        marker in text
        for marker in (
            "challenge page",
            "robotabuse",
            "cloudflare",
            "just a moment",
            "__cf_chl",
            "cdn-cgi/challenge",
        )
    )


def row_counts_toward_success(row: dict[str, str], args: argparse.Namespace) -> bool:
    if row.get("status") != "ok":
        return False
    if args.stage in {"collect", "all"}:
        if args.fetch_only:
            app = str(row.get("application_number") or "")
            if not app:
                return False
            case_dir = Path(args.output_root) / app
            if args.fetch_success_policy == "raw-complete":
                return raw_complete_artifacts_exist(case_dir)
            if args.fetch_success_policy == "doclist-complete":
                return doclist_complete_artifacts_exist(case_dir)
            return fetch_artifacts_exist(case_dir)
        benchmark_input = row.get("benchmark_input") or ""
        if not benchmark_input or not Path(benchmark_input).exists():
            return False
    if args.stage in {"analysis", "all"}:
        analysis_json = row.get("analysis_json") or ""
        analysis_html = row.get("analysis_html") or ""
        if not analysis_json or not analysis_html:
            return False
        if not Path(analysis_json).exists() or not Path(analysis_html).exists():
            return False
    return True


def count_successes(rows_by_app: dict[str, dict[str, str]], apps: set[str], args: argparse.Namespace) -> int:
    return sum(1 for app, row in rows_by_app.items() if app in apps and row_counts_toward_success(row, args))


def write_single_record_manifest(record: dict[str, Any], app: str, output_root: Path) -> Path:
    state_dir = output_root / "_state" / "single-record-manifests"
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = state_dir / f"{app}.json"
    payload = {
        "metadata": {
            "created_for": "single_case_publication_server_original_fetch",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "records": [record],
    }
    write_json(manifest_path, payload)
    return manifest_path


def run_publication_server_original_fetch(
    app: str,
    record: dict[str, Any],
    args: argparse.Namespace,
    project_root: Path,
    output_root: Path,
) -> None:
    publication_number = str(record.get("publication_number") or "")
    if not publication_number:
        print(f"[publication-server] {app} skipped: missing publication_number", flush=True)
        return
    manifest_path = write_single_record_manifest(record, app, output_root)
    status_dir = output_root / "_state"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_path = status_dir / f"publication-server-{app}.csv"
    fetch_args = [
        sys.executable,
        "scripts\\fetch_publication_server_pdfs.py",
        "--manifest",
        str(manifest_path),
        "--output-root",
        args.output_root,
        "--status-file",
        str(status_path),
        "--skip-existing",
        "--only-missing-original",
        "--timeout",
        str(args.publication_server_timeout),
        "--delay-seconds",
        str(args.publication_server_delay_seconds),
    ]
    if args.publication_server_fallback_formats:
        fetch_args += ["--fallback-formats", args.publication_server_fallback_formats]
    print(f"[publication-server] {app} original-application fallback", flush=True)
    run_command(fetch_args, cwd=project_root)


def run_collect(app: str, record: dict[str, Any], args: argparse.Namespace, project_root: Path, output_root: Path) -> None:
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
        "-EpoRetryCount",
        str(args.epo_retry_count),
        "-EpoRetryDelaySeconds",
        str(args.epo_retry_delay_seconds),
        "-EpoRequestDelayMilliseconds",
        str(args.epo_request_delay_milliseconds),
        "-EpoRequestTimeoutSeconds",
        str(args.epo_request_timeout_seconds),
    ]
    if args.fetch_only:
        collect_args.append("-FetchOnly")
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
    if args.extract_pdf_text and not args.fetch_only:
        collect_args.append("-ExtractPdfText")
    if args.epo_proxy_url:
        collect_args += ["-EpoProxyUrl", args.epo_proxy_url]
    if args.doclist_cache_root:
        collect_args += ["-DoclistCacheRoot", args.doclist_cache_root]
    if args.skip_register_main_fetch:
        collect_args.append("-SkipRegisterMainFetch")
    if args.browser_register_fallback:
        collect_args.append("-BrowserRegisterFallback")
        collect_args += ["-BrowserProfileDir", args.browser_profile_dir]
        if args.browser_proxy_server:
            collect_args += ["-BrowserProxyServer", args.browser_proxy_server]
        collect_args += ["-BrowserManualWaitSeconds", str(args.browser_manual_wait_seconds)]
        if args.browser_start_minimized:
            collect_args.append("-BrowserStartMinimized")
    run_command(collect_args, cwd=project_root)
    if not args.skip_publication_server_original:
        original_dir = output_root / app / "original-application"
        if valid_index_pdf_count(output_root / app, original_dir) > 0:
            print(f"[publication-server] {app} skipped: original-application already has valid PDF(s)", flush=True)
        else:
            run_publication_server_original_fetch(app, record, args, project_root, output_root)
        if not args.fetch_only:
            case_dir = output_root / app
            benchmark_input = case_dir / f"{app}-benchmark-input.json"
            if benchmark_input.exists():
                rebuild_benchmark_input(app, case_dir, benchmark_input, args, project_root)
    if args.fetch_only:
        case_dir = output_root / app
        if args.fetch_success_policy == "raw-complete":
            if not raw_complete_artifacts_exist(case_dir):
                raise RuntimeError(
                    f"Raw fetch incomplete for {app}: expected register main/doclist, docs PDF, and original-application PDF."
                )
        elif args.fetch_success_policy == "doclist-complete":
            if not doclist_complete_artifacts_exist(case_dir):
                raise RuntimeError(
                    f"Raw fetch incomplete for {app}: expected cached/fetched doclist CSV, docs PDF, and original-application PDF."
                )
        elif not fetch_artifacts_exist(case_dir):
            raise RuntimeError(f"Raw fetch incomplete for {app}: expected at least one valid PDF.")


def read_download_index(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def log_downloaded_case_files(app: str, case_dir: Path) -> None:
    print(f"[case-files] {app} case_dir={case_dir}", flush=True)
    for label, folder in [("docs", case_dir / "docs"), ("original-application", case_dir / "original-application")]:
        rows = read_download_index(folder / "download-index.csv")
        print(f"[case-files] {app} {label}_count={len(rows)}", flush=True)
        if not rows:
            continue
        for row in rows:
            title = str(row.get("title") or "")
            date = str(row.get("date") or "")
            file_name = str(row.get("fileName") or "")
            path = str(row.get("path") or "")
            pages = str(row.get("pages") or "")
            document_id = str(row.get("documentId") or "")
            print(
                f"[case-file] {app} bucket={label} date={date} pages={pages} "
                f"documentId={document_id} title={title} fileName={file_name} path={path}",
                flush=True,
            )


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
            "--retries",
            str(args.retries),
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
    if args.analysis_mode == "split" and args.complete_split_analysis:
        complete_args = [
            sys.executable,
            "scripts\\complete_analysis_json.py",
            str(benchmark_input),
            str(analysis_json),
            "--project-root",
            str(project_root),
            "--env-file",
            args.env_file,
            "--api-key-env",
            args.api_key_env,
            "--base-url",
            args.base_url,
            "--model",
            args.model,
            "--max-source-files",
            str(args.complete_max_source_files),
            "--max-chars-per-file",
            str(args.complete_max_chars_per_file),
            "--max-field-chars",
            str(args.complete_max_field_chars),
            "--max-prior-art",
            str(args.complete_max_prior_art),
            "--max-tokens",
            str(args.complete_max_tokens),
            "--meta-max-tokens",
            str(args.complete_meta_max_tokens),
            "--request-timeout",
            str(args.complete_request_timeout),
            "--retries",
            str(args.complete_retries),
            "--passes",
            str(args.complete_passes),
            "--reasoning-effort",
            args.complete_reasoning_effort,
            "--verbosity",
            args.complete_verbosity,
        ]
        run_command(complete_args, cwd=project_root)
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


def rotate_clash_node(args: argparse.Namespace, project_root: Path, app: str, attempt: int) -> bool:
    if not args.clash_auto_rotate:
        return False
    rotate_args = [
        sys.executable,
        "scripts\\rotate_clash_proxy.py",
        "--config",
        args.clash_config,
        "--controller",
        args.clash_controller,
        "--selector",
        args.clash_selector,
        "--history-file",
        args.clash_history_file,
        "--reason",
        f"collect_failure:{app}:attempt{attempt}",
        "--bad-cooldown-seconds",
        str(args.clash_bad_cooldown_seconds),
    ]
    if args.clash_secret:
        rotate_args += ["--secret", args.clash_secret]
    if args.clash_include_regex:
        rotate_args += ["--include-regex", args.clash_include_regex]
    if args.clash_exclude_regex:
        rotate_args += ["--exclude-regex", args.clash_exclude_regex]
    try:
        run_command(rotate_args, cwd=project_root)
    except Exception as exc:
        print(f"[clash-rotate-error] {app}: {exc!r}", flush=True)
        return False
    if args.clash_rotate_sleep_seconds > 0:
        time.sleep(args.clash_rotate_sleep_seconds)
    return True


def run_record(index: int, record: dict[str, Any], args: argparse.Namespace, project_root: Path, output_root: Path) -> dict[str, str]:
    app = str(record["application_number"]).split(".")[0]
    case_dir = output_root / app
    benchmark_input = case_dir / f"{app}-benchmark-input.json"
    analysis_json = case_dir / f"{app}-analysis.json"
    analysis_html = case_dir / f"{app}-analysis.html"
    started = datetime.now(timezone.utc).isoformat()
    status = "ok"
    error = ""

    max_attempts = 1 + max(0, args.clash_retries_per_record if args.clash_auto_rotate else 0)
    for attempt in range(1, max_attempts + 1):
        try:
            if args.stage in {"collect", "all"}:
                existing_fetch_ok = (
                    raw_complete_artifacts_exist(case_dir)
                    if args.fetch_success_policy == "raw-complete"
                    else doclist_complete_artifacts_exist(case_dir)
                    if args.fetch_success_policy == "doclist-complete"
                    else fetch_artifacts_exist(case_dir)
                )
                if args.skip_existing and args.fetch_only and existing_fetch_ok:
                    print(f"[skip] {app} already satisfies fetch success policy ({args.fetch_success_policy})", flush=True)
                elif args.skip_existing and not args.fetch_only and benchmark_input.exists():
                    print(f"[skip] {app} already has benchmark input", flush=True)
                else:
                    run_collect(app, record, args, project_root, output_root)
                log_downloaded_case_files(app, case_dir)

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
            status = "ok"
            error = ""
            break
        except Exception as exc:
            status = "error"
            error = repr(exc)
            print(f"[error] {app} attempt={attempt}/{max_attempts}: {error}", flush=True)
            if attempt < max_attempts and args.stage in {"collect", "all"} and rotate_clash_node(args, project_root, app, attempt):
                print(f"[retry-after-clash-rotate] {app} attempt={attempt + 1}/{max_attempts}", flush=True)
                continue
            break

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


def run_all_records(
    indexed_records: list[tuple[int, dict[str, Any]]],
    rows_by_app: dict[str, dict[str, str]],
    status_path: Path,
    args: argparse.Namespace,
    project_root: Path,
    output_root: Path,
) -> None:
    workers = max(1, args.workers)
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


def run_until_success_target(
    indexed_records: list[tuple[int, dict[str, Any]]],
    rows_by_app: dict[str, dict[str, str]],
    status_path: Path,
    args: argparse.Namespace,
    project_root: Path,
    output_root: Path,
) -> None:
    workers = max(1, args.workers)
    candidate_apps = {str(record["application_number"]).split(".")[0] for _, record in indexed_records}
    successes = count_successes(rows_by_app, candidate_apps, args)
    print(
        f"Running until {args.success_target} successful record(s) with {workers} worker(s). "
        f"Already successful: {successes}. Status: {status_path}",
        flush=True,
    )
    if successes >= args.success_target:
        print(f"Success target already reached: {successes}/{args.success_target}", flush=True)
        return

    pending = iter(indexed_records)
    in_flight = {}
    circuit_open = False

    with ThreadPoolExecutor(max_workers=workers) as executor:
        def submit_next() -> bool:
            for index, record in pending:
                app = str(record["application_number"]).split(".")[0]
                existing = rows_by_app.get(app)
                if existing and row_counts_toward_success(existing, args):
                    continue
                future = executor.submit(run_record, index, record, args, project_root, output_root)
                in_flight[future] = (index, app)
                return True
            return False

        while (
            not circuit_open
            and len(in_flight) < workers
            and successes + len(in_flight) < args.success_target
            and submit_next()
        ):
            pass

        while in_flight and successes < args.success_target and not circuit_open:
            done, _ = wait(set(in_flight), return_when=FIRST_COMPLETED)
            for future in done:
                index, app = in_flight.pop(future)
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
                successes = count_successes(rows_by_app, candidate_apps, args)
                print(f"[done] {app}: {row['status']} successes={successes}/{args.success_target}", flush=True)
                if args.stop_on_global_block and global_fetch_blocked(row.get("error") or ""):
                    circuit_open = True
                    print(
                        f"[circuit-open] Global EPO access block detected while processing {app}. "
                        "Stopping new submissions.",
                        flush=True,
                    )

            if circuit_open:
                for pending_future in in_flight:
                    pending_future.cancel()
                break

            while (
                len(in_flight) < workers
                and successes + len(in_flight) < args.success_target
                and submit_next()
            ):
                pass

    if circuit_open:
        print("WARNING: stopped early because the global EPO access circuit breaker opened.", flush=True)
        return

    if successes < args.success_target:
        print(
            f"WARNING: success target not reached: {successes}/{args.success_target}. "
            "Enlarge the verified manifest and rerun.",
            flush=True,
        )


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
    parser.add_argument("--max-source-files", type=int, default=8)
    parser.add_argument("--max-chars-per-file", type=int, default=5000)
    parser.add_argument("--max-field-chars", type=int, default=5000)
    parser.add_argument("--max-prior-art", type=int, default=12)
    parser.add_argument("--max-tokens", type=int, default=3000)
    parser.add_argument("--meta-max-tokens", type=int, default=1200)
    parser.add_argument("--request-timeout", type=int, default=240)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--verbosity", default="low")
    parser.add_argument("--write-analysis-steps", action="store_true")
    parser.add_argument("--complete-split-analysis", dest="complete_split_analysis", action="store_true", default=True)
    parser.add_argument("--skip-complete-split-analysis", dest="complete_split_analysis", action="store_false")
    parser.add_argument("--complete-max-source-files", type=int, default=8)
    parser.add_argument("--complete-max-chars-per-file", type=int, default=5000)
    parser.add_argument("--complete-max-field-chars", type=int, default=5000)
    parser.add_argument("--complete-max-prior-art", type=int, default=12)
    parser.add_argument("--complete-max-tokens", type=int, default=3000)
    parser.add_argument("--complete-meta-max-tokens", type=int, default=1200)
    parser.add_argument("--complete-request-timeout", type=int, default=240)
    parser.add_argument("--complete-retries", type=int, default=1)
    parser.add_argument("--complete-passes", type=int, default=2)
    parser.add_argument("--complete-reasoning-effort", default="low")
    parser.add_argument("--complete-verbosity", default="low")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--fetch-only", action="store_true", help="Collect only network artifacts: register pages, doclist CSVs, and PDFs. Skip text extraction, OCR, benchmark input build, and rendering.")
    parser.add_argument("--epo-proxy-url", default="", help="Explicit proxy URL for EPO Register and document downloads, for example http://127.0.0.1:7897.")
    parser.add_argument("--epo-retry-count", type=int, default=4)
    parser.add_argument("--epo-retry-delay-seconds", type=int, default=3)
    parser.add_argument("--epo-request-delay-milliseconds", type=int, default=1200)
    parser.add_argument("--epo-request-timeout-seconds", type=int, default=60)
    parser.add_argument("--browser-register-fallback", action="store_true", help="Use persistent browser fallback when EPO Register main/doclist ordinary HTTP fetch is challenged.")
    parser.add_argument("--browser-profile-dir", default="markush-run/_state/epo-register-browser-profile")
    parser.add_argument("--browser-proxy-server", default="")
    parser.add_argument("--browser-manual-wait-seconds", type=int, default=90)
    parser.add_argument("--browser-start-minimized", action="store_true")
    parser.add_argument("--doclist-cache-root", default="", help="Copy validated doclist CSV/HTML from this cache before collect, avoiding another register doclist fetch.")
    parser.add_argument("--skip-register-main-fetch", action="store_true", help="Skip fetching EPO Register main.html during collect. Use with --fetch-success-policy doclist-complete.")
    parser.add_argument("--skip-publication-server-original", action="store_true", help="Do not use the EPO Publication Server to fill missing original-application PDFs during --fetch-only collect.")
    parser.add_argument("--publication-server-timeout", type=int, default=120)
    parser.add_argument("--publication-server-delay-seconds", type=float, default=0.0)
    parser.add_argument("--publication-server-fallback-formats", default="html,xml,zip")
    parser.add_argument("--clash-auto-rotate", action="store_true", help="Rotate a Clash/Mihomo selector after a collect failure, then retry the same record.")
    parser.add_argument("--clash-config", default="")
    parser.add_argument("--clash-controller", default="127.0.0.1:9090")
    parser.add_argument("--clash-secret", default="")
    parser.add_argument("--clash-selector", default="节点选择")
    parser.add_argument("--clash-history-file", default="markush-run/_state/clash-node-rotation.json")
    parser.add_argument("--clash-retries-per-record", type=int, default=1)
    parser.add_argument("--clash-rotate-sleep-seconds", type=float, default=10.0)
    parser.add_argument("--clash-bad-cooldown-seconds", type=int, default=1800)
    parser.add_argument("--clash-include-regex", default="")
    parser.add_argument("--clash-exclude-regex", default="")
    parser.add_argument(
        "--fetch-success-policy",
        choices=["raw-complete", "doclist-complete", "any-pdf"],
        default="raw-complete",
        help="When --fetch-only is used, choose the artifact set required for success. doclist-complete requires doclist CSV, docs PDF, and original-application PDF, without register main.html.",
    )
    parser.add_argument("--stop-on-global-block", action="store_true", default=True, help="Stop submitting new collect jobs when an EPO challenge/RobotAbuse block is detected.")
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
    parser.add_argument(
        "--success-target",
        type=int,
        default=0,
        help="Stop submitting new records once this many successful records exist. 0 preserves the old run-all behavior.",
    )
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    records = load_records(project_root / args.manifest, args.limit, args.offset)
    output_root = project_root / args.output_root
    if args.status_file:
        status_arg = Path(args.status_file)
        if status_arg.is_absolute():
            status_path = status_arg
        elif len(status_arg.parts) > 1:
            status_path = project_root / status_arg
        else:
            status_path = output_root / status_arg
    else:
        status_path = output_root / ("batch-status.csv" if args.stage == "all" else f"batch-{args.stage}-status.csv")
    output_root.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)

    rows_by_app: dict[str, dict[str, str]] = {}
    if status_path.exists():
        with status_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                app = str(row.get("application_number") or "")
                if app:
                    rows_by_app[app] = row

    indexed_records = list(enumerate(records, start=args.offset + 1))
    try:
        if args.success_target:
            run_until_success_target(indexed_records, rows_by_app, status_path, args, project_root, output_root)
        else:
            run_all_records(indexed_records, rows_by_app, status_path, args, project_root, output_root)
    finally:
        print(f"Wrote status: {status_path}")
        try:
            run_command(
                [sys.executable, str(project_root / "scripts" / "collection" / "build_benchmark_overview.py")],
                project_root,
            )
        except subprocess.CalledProcessError as exc:
            print(f"WARNING: benchmark overview refresh failed with exit code {exc.returncode}.", file=sys.stderr)


if __name__ == "__main__":
    main()

