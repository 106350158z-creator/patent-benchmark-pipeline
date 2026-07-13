from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(args: list[str], cwd: Path) -> None:
    print("[run]", " ".join(args), flush=True)
    subprocess.run(args, cwd=str(cwd), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the target-keyword EPO raw-material collection workflow.")
    parser.add_argument(
        "--candidate-source",
        choices=["manifest", "google"],
        default="manifest",
        help="manifest reuses an existing verified manifest and skips candidate discovery.",
    )
    parser.add_argument("--candidate-pool", default="markush-run/benchmark/ep_application_candidates_epo_target_pool.json")
    parser.add_argument("--manifest", default="markush-run/benchmark/ep_review_file_sources_target500.json")
    parser.add_argument("--output-root", default="markush-run/benchmark-target500")
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--candidate-limit", type=int, default=2000)
    parser.add_argument("--pages-per-query", type=int, default=5)
    parser.add_argument("--max-detail", type=int, default=3000)
    parser.add_argument("--candidate-workers", type=int, default=12)
    parser.add_argument("--validation-workers", type=int, default=2)
    parser.add_argument("--collect-workers", type=int, default=2)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://yunwu.ai/v1")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--skip-candidate-collection", action="store_true")
    parser.add_argument("--skip-manifest-validation", action="store_true")
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--include-prior-art-download", action="store_true", help="Also download prior-art PDFs after the core raw-material collection. Core full collection does not require prior art.")
    parser.add_argument("--allow-google-prior-art-fallback", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    parser.add_argument("--process-local", action="store_true", help="After network collection, extract embedded PDF text during collect. Default is raw fetch only: no OCR, no parsing, no rendering.")
    parser.add_argument("--epo-proxy-url", default="", help="Explicit proxy URL for EPO Register and document downloads, for example http://127.0.0.1:7897.")
    parser.add_argument("--epo-retry-count", type=int, default=4)
    parser.add_argument("--epo-retry-delay-seconds", type=int, default=3)
    parser.add_argument("--epo-request-delay-milliseconds", type=int, default=1200)
    parser.add_argument("--epo-request-timeout-seconds", type=int, default=60)
    parser.add_argument("--browser-doclist-fallback", action="store_true", help="Use persistent browser session for EPO Register doclist validation when ordinary HTTP is challenged.")
    parser.add_argument("--browser-profile-dir", default="markush-run/_state/epo-register-browser-profile")
    parser.add_argument("--browser-proxy-server", default="")
    parser.add_argument("--browser-headless", action="store_true")
    parser.add_argument("--browser-manual-wait-seconds", type=int, default=180)
    parser.add_argument("--doclist-cache-root", default="")
    parser.add_argument("--skip-register-main-fetch", action="store_true")
    parser.add_argument("--fetch-success-policy", choices=["raw-complete", "doclist-complete", "any-pdf"], default="raw-complete")
    parser.add_argument("--clash-auto-rotate", action="store_true", help="Rotate Clash/Mihomo node after collect failures.")
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
    args = parser.parse_args()

    project_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "README.md").exists() and (parent / "scripts").exists())
    py = sys.executable

    if args.candidate_source == "manifest":
        print(f"[reuse-manifest] {args.manifest}", flush=True)
        args.skip_candidate_collection = True
        args.skip_manifest_validation = True

    if not args.skip_candidate_collection:
        run(
            [
                py,
                "scripts\\collect_ep_application_candidates.py",
                "-o",
                args.candidate_pool,
                "--limit",
                str(args.candidate_limit),
                "--pages-per-query",
                str(args.pages_per_query),
                "--max-detail",
                str(args.max_detail),
                "--detail-workers",
                str(args.candidate_workers),
            ],
            cwd=project_root,
        )

    if not args.skip_manifest_validation:
        validation_args = [
            py,
            "scripts\\build_target_review_manifest.py",
            "--candidates",
            args.candidate_pool,
            "--output",
            args.manifest,
            "--target",
            str(args.target),
            "--workers",
            str(args.validation_workers),
        ]
        if args.epo_proxy_url:
            validation_args += ["--epo-proxy-url", args.epo_proxy_url]
        if args.browser_doclist_fallback:
            validation_args += [
                "--browser-doclist-fallback",
                "--browser-profile-dir",
                args.browser_profile_dir,
                "--browser-manual-wait-seconds",
                str(args.browser_manual_wait_seconds),
            ]
            browser_proxy_server = args.browser_proxy_server or args.epo_proxy_url
            if browser_proxy_server:
                validation_args += ["--browser-proxy-server", browser_proxy_server]
            if args.browser_headless:
                validation_args.append("--browser-headless")
        run(validation_args, cwd=project_root)

    if not args.skip_collect:
        collect_args = [
                py,
                "scripts\\run_manifest_benchmark_batch.py",
                "--manifest",
                args.manifest,
                "--output-root",
                args.output_root,
                "--stage",
                "collect",
                "--skip-existing",
                "--success-target",
                str(args.target),
                "--workers",
                str(args.collect_workers),
                "--env-file",
                args.env_file,
                "--api-key-env",
                args.api_key_env,
                "--base-url",
                args.base_url,
                "--model",
                args.model,
                "--epo-retry-count",
                str(args.epo_retry_count),
                "--epo-retry-delay-seconds",
                str(args.epo_retry_delay_seconds),
                "--epo-request-delay-milliseconds",
                str(args.epo_request_delay_milliseconds),
                "--epo-request-timeout-seconds",
                str(args.epo_request_timeout_seconds),
        ]
        if args.process_local:
            collect_args.append("--extract-pdf-text")
        else:
            collect_args.append("--fetch-only")
        if args.epo_proxy_url:
            collect_args += ["--epo-proxy-url", args.epo_proxy_url]
        if args.doclist_cache_root:
            collect_args += ["--doclist-cache-root", args.doclist_cache_root]
        if args.skip_register_main_fetch:
            collect_args.append("--skip-register-main-fetch")
        if args.fetch_success_policy:
            collect_args += ["--fetch-success-policy", args.fetch_success_policy]
        if args.browser_doclist_fallback:
            collect_args += [
                "--browser-register-fallback",
                "--browser-profile-dir",
                args.browser_profile_dir,
                "--browser-manual-wait-seconds",
                str(args.browser_manual_wait_seconds),
                "--browser-start-minimized",
            ]
            browser_proxy_server = args.browser_proxy_server or args.epo_proxy_url
            if browser_proxy_server:
                collect_args += ["--browser-proxy-server", browser_proxy_server]
        if args.clash_auto_rotate:
            collect_args += [
                "--clash-auto-rotate",
                "--clash-config",
                args.clash_config,
                "--clash-controller",
                args.clash_controller,
                "--clash-selector",
                args.clash_selector,
                "--clash-history-file",
                args.clash_history_file,
                "--clash-retries-per-record",
                str(args.clash_retries_per_record),
                "--clash-rotate-sleep-seconds",
                str(args.clash_rotate_sleep_seconds),
                "--clash-bad-cooldown-seconds",
                str(args.clash_bad_cooldown_seconds),
            ]
            if args.clash_secret:
                collect_args += ["--clash-secret", args.clash_secret]
            if args.clash_include_regex:
                collect_args += ["--clash-include-regex", args.clash_include_regex]
            if args.clash_exclude_regex:
                collect_args += ["--clash-exclude-regex", args.clash_exclude_regex]
        run(collect_args, cwd=project_root)

    if args.include_prior_art_download:
        prior_art_args = [py, "scripts\\download_prior_art_pdfs.py", args.output_root]
        if args.allow_google_prior_art_fallback:
            prior_art_args.append("--allow-google-fallback")
        run(prior_art_args, cwd=project_root)

    if not args.skip_audit:
        run(
            [
                py,
                "scripts\\audit_raw_materials.py",
                args.output_root,
                "--manifest",
                args.manifest,
            ],
            cwd=project_root,
        )


if __name__ == "__main__":
    main()

