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
    parser.add_argument("--skip-prior-art-download", action="store_true")
    parser.add_argument("--allow-google-prior-art-fallback", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
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
        run(
            [
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
            ],
            cwd=project_root,
        )

    if not args.skip_collect:
        run(
            [
                py,
                "scripts\\run_manifest_benchmark_batch.py",
                "--manifest",
                args.manifest,
                "--output-root",
                args.output_root,
                "--stage",
                "collect",
                "--extract-pdf-text",
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
            ],
            cwd=project_root,
        )

    if not args.skip_prior_art_download:
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
