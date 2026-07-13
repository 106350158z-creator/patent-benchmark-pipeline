from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


STATUS_FIELDS = [
    "category",
    "application_number",
    "status",
    "started_at_utc",
    "finished_at_utc",
    "claims_json",
    "claims_review_html",
    "benchmark_input",
    "analysis_json",
    "analysis_html",
    "error",
]


def discover_case_dirs(root: Path) -> list[Path]:
    if root.name.startswith("EP") and (root / "register").exists() and (root / "docs").exists():
        return [root]
    return sorted(
        path
        for path in root.rglob("EP*")
        if path.is_dir() and (path / "register").exists() and (path / "docs").exists()
    )


def run_command(args: list[str], cwd: Path) -> None:
    print("[run]", " ".join(args), flush=True)
    subprocess.run(args, cwd=str(cwd), check=True)


def run_optional(args: list[str], cwd: Path, *, required: bool) -> str:
    try:
        run_command(args, cwd)
    except subprocess.CalledProcessError as exc:
        if required:
            raise
        return repr(exc)
    return ""


def refresh_case(case_dir: Path, root: Path, args: argparse.Namespace, project_root: Path) -> dict[str, str]:
    app = case_dir.name
    try:
        category = str(case_dir.parent.relative_to(root)) if case_dir.parent != root else ""
    except ValueError:
        category = ""
    started = datetime.now(timezone.utc).isoformat()
    benchmark_input = case_dir / f"{app}-benchmark-input.json"
    claims_json = case_dir / f"{app}-claims-verified.json"
    claims_review_html = case_dir / f"{app}-claims-review.html"
    analysis_json = case_dir / f"{app}-analysis.json"
    analysis_html = case_dir / f"{app}-analysis.html"
    errors: list[str] = []
    status = "ok"

    try:
        if args.extract_pdf_text:
            extract_args = [sys.executable, "scripts/extract_pdf_text.py", str(case_dir / "docs")]
            if args.overwrite_pdf_text:
                extract_args.append("--overwrite")
            run_command(extract_args, project_root)

        if args.run_ocr:
            ocr_args = [
                sys.executable,
                "scripts/ocr_case_batch.py",
                str(case_dir),
                "--scope",
                args.ocr_scope,
                "--workers",
                str(args.ocr_workers),
                "--timeout",
                str(args.ocr_timeout_seconds),
                "--zoom",
                str(args.ocr_zoom),
                "--include-regex",
                args.ocr_include_regex,
                "--exclude-regex",
                args.ocr_exclude_regex,
            ]
            if args.overwrite_ocr:
                ocr_args.append("--overwrite")
            errors.append(run_optional(ocr_args, project_root, required=not args.continue_on_ocr_error))

        if args.generate_claims_review:
            claims_args = [
                sys.executable,
                "scripts/generate_claims_verified.py",
                str(case_dir),
                "--zoom",
                str(args.claims_review_zoom),
            ]
            if args.overwrite_claims_images:
                claims_args.append("--overwrite-images")
            if args.clear_claims_images:
                claims_args.append("--clear-images")
            if args.overwrite_claims_json:
                claims_args.append("--overwrite-json")
            run_command(claims_args, project_root)

        build_args = [
            sys.executable,
            "scripts/build_benchmark_input.py",
            str(case_dir),
            "--application-number",
            app,
            "--top-k",
            str(args.top_k),
            "-o",
            str(benchmark_input),
        ]
        run_command(build_args, project_root)

        if args.render_markush_pages:
            render_args = [
                sys.executable,
                "scripts/render_markush_pages.py",
                str(benchmark_input),
                "--max-pages",
                str(args.markush_max_pages),
                "--candidate-limit",
                str(args.markush_candidate_limit),
                "--selected-limit",
                str(args.markush_selected_limit),
                "--zoom",
                str(args.markush_zoom),
            ]
            if args.clear_markush_assets:
                render_args.append("--clear")
            run_command(render_args, project_root)

        if args.stage in {"all", "analysis"}:
            analysis_args = [
                sys.executable,
                "scripts/generate_analysis_json_split.py",
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
            if args.allow_low_quality_source:
                analysis_args.append("--allow-low-quality-source")
            run_command(analysis_args, project_root)

            if args.complete_split_analysis:
                complete_args = [
                    sys.executable,
                    "scripts/complete_analysis_json.py",
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
                if args.allow_low_quality_source:
                    complete_args.append("--allow-low-quality-source")
                run_command(complete_args, project_root)

            if args.translate_lists:
                run_command(
                    [
                        sys.executable,
                        "scripts/translate_report_lists.py",
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
                    project_root,
                )

            if args.repair_evidence:
                run_command(
                    [
                        sys.executable,
                        "scripts/repair_report_sources.py",
                        str(analysis_json),
                        "--case-dir",
                        str(case_dir),
                        "--min-verified-score",
                        str(args.verify_min_score),
                        "--min-repair-score",
                        str(args.repair_min_score),
                    ],
                    project_root,
                )

            run_command(
                [
                    sys.executable,
                    "scripts/ensure_html_field_completeness.py",
                    str(analysis_json),
                ],
                project_root,
            )

            verify_error = run_optional(
                [
                    sys.executable,
                    "scripts/verify_report_sources.py",
                    str(analysis_json),
                    "--case-dir",
                    str(case_dir),
                    "--min-score",
                    str(args.verify_min_score),
                ],
                project_root,
                required=not args.continue_on_verify_error,
            )
            if verify_error:
                errors.append(verify_error)
                status = "needs_review"

            run_command(
                [
                    sys.executable,
                    "scripts/download_prior_art_pdfs.py",
                    str(benchmark_input),
                ],
                project_root,
            )

            run_command(
                [
                    sys.executable,
                    "scripts/json_to_html_report.py",
                    str(analysis_json),
                    "-o",
                    str(analysis_html),
                    "--benchmark-input",
                    str(benchmark_input),
                ],
                project_root,
            )
    except Exception as exc:
        status = "error"
        errors.append(repr(exc))

    return {
        "category": category,
        "application_number": app,
        "status": status,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "claims_json": str(claims_json),
        "claims_review_html": str(claims_review_html),
        "benchmark_input": str(benchmark_input),
        "analysis_json": str(analysis_json),
        "analysis_html": str(analysis_html),
        "error": "; ".join(error for error in errors if error),
    }


def write_status(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh an existing nested EPO case set: OCR, rebuild inputs, generate analysis, verify evidence, and render HTML.")
    parser.add_argument("root", help="Case directory or nested root containing EP case directories.")
    parser.add_argument("--stage", choices=["prepare", "analysis", "all"], default="all")
    parser.add_argument("--status-file", default="", help="CSV status output. Defaults to <root>/_refresh_status.csv.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://yunwu.ai/v1")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--render-markush-pages", dest="render_markush_pages", action="store_true", default=True)
    parser.add_argument("--skip-render-markush-pages", dest="render_markush_pages", action="store_false")
    parser.add_argument("--markush-max-pages", type=int, default=6)
    parser.add_argument("--markush-candidate-limit", type=int, default=36)
    parser.add_argument("--markush-selected-limit", type=int, default=3)
    parser.add_argument("--markush-zoom", type=float, default=2.4)
    parser.add_argument("--clear-markush-assets", action="store_true", default=True)
    parser.add_argument("--extract-pdf-text", dest="extract_pdf_text", action="store_true", default=True)
    parser.add_argument("--skip-extract-pdf-text", dest="extract_pdf_text", action="store_false")
    parser.add_argument("--overwrite-pdf-text", action="store_true", default=True)
    parser.add_argument("--run-ocr", dest="run_ocr", action="store_true", default=True)
    parser.add_argument("--skip-ocr", dest="run_ocr", action="store_false")
    parser.add_argument("--ocr-scope", choices=["docs", "original", "both"], default="docs")
    parser.add_argument("--ocr-include-regex", default="claims|amended_claims|communication|decision|annex|reply|search_opinion|search_report|summons|grounds")
    parser.add_argument("--ocr-exclude-regex", default="translation|description|published_international")
    parser.add_argument("--ocr-workers", type=int, default=1)
    parser.add_argument("--ocr-timeout-seconds", type=int, default=1200)
    parser.add_argument("--ocr-zoom", type=float, default=1.8)
    parser.add_argument("--overwrite-ocr", action="store_true")
    parser.add_argument("--continue-on-ocr-error", action="store_true")
    parser.add_argument("--generate-claims-review", dest="generate_claims_review", action="store_true", default=True)
    parser.add_argument("--skip-generate-claims-review", dest="generate_claims_review", action="store_false")
    parser.add_argument("--claims-review-zoom", type=float, default=1.8)
    parser.add_argument("--overwrite-claims-images", dest="overwrite_claims_images", action="store_true", default=True)
    parser.add_argument("--keep-claims-images", dest="overwrite_claims_images", action="store_false")
    parser.add_argument("--clear-claims-images", dest="clear_claims_images", action="store_true", default=True)
    parser.add_argument("--keep-stale-claims-images", dest="clear_claims_images", action="store_false")
    parser.add_argument("--overwrite-claims-json", action="store_true", help="Regenerate draft JSON. Do not use after human verification unless intentional.")
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
    parser.add_argument("--allow-low-quality-source", action="store_true")
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
    parser.add_argument("--translate-lists", action="store_true")
    parser.add_argument("--repair-evidence", dest="repair_evidence", action="store_true", default=True)
    parser.add_argument("--skip-repair-evidence", dest="repair_evidence", action="store_false")
    parser.add_argument("--repair-min-score", type=float, default=0.55)
    parser.add_argument("--verify-min-score", type=float, default=0.88)
    parser.add_argument("--continue-on-verify-error", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    root = Path(args.root)
    status_path = Path(args.status_file) if args.status_file else root / "_refresh_status.csv"
    rows: list[dict[str, str]] = []
    case_dirs = discover_case_dirs(root)
    print(f"Refreshing {len(case_dirs)} case(s) under {root}", flush=True)
    for case_dir in case_dirs:
        row = refresh_case(case_dir, root, args, project_root)
        rows.append(row)
        write_status(status_path, rows)
        print(f"[done] {row['application_number']}: {row['status']}", flush=True)
        if row["status"] == "error" and not args.continue_on_error:
            break

    run_command(
            [
                sys.executable,
                "scripts/audit_case_quality.py",
                str(root),
                "-o",
                str(root / "_quality_audit.csv"),
        ],
        project_root,
    )
    run_command(
        [
            sys.executable,
            "scripts/validate_case_set_completeness.py",
            str(root),
            "-o",
            str(root / "_completeness_validation.csv"),
        ],
        project_root,
    )
    print(f"Wrote status CSV: {status_path}")


if __name__ == "__main__":
    main()

