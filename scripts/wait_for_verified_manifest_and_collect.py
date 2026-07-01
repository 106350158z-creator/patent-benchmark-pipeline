from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def start_collect(args: argparse.Namespace, project_root: Path) -> Path:
    log_root = (project_root / args.log_root).resolve()
    log_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_log = log_root / f"target500-auto-collect-{stamp}.out.log"
    err_log = log_root / f"target500-auto-collect-{stamp}.err.log"
    pid_file = log_root / f"target500-auto-collect-{stamp}.pid.txt"
    command = [
        sys.executable,
        "scripts\\run_target_benchmark_raw_materials.py",
        "--candidate-source",
        "manifest",
        "--manifest",
        args.manifest,
        "--output-root",
        args.output_root,
        "--target",
        str(args.target),
        "--collect-workers",
        str(args.collect_workers),
    ]
    if args.skip_prior_art_download:
        command.append("--skip-prior-art-download")
    if args.skip_audit:
        command.append("--skip-audit")
    with out_log.open("w", encoding="utf-8") as stdout, err_log.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, cwd=str(project_root), stdout=stdout, stderr=stderr)
    pid_file.write_text(
        "\n".join(
            [
                f"pid={process.pid}",
                f"command={' '.join(command)}",
                f"stdout={out_log}",
                f"stderr={err_log}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[started] pid={process.pid} stdout={out_log} stderr={err_log}", flush=True)
    return pid_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait until a verified manifest reaches target size, then start collection.")
    parser.add_argument("--manifest", default="markush-run/benchmark/ep_review_file_sources_verified500_keywords.json")
    parser.add_argument("--output-root", default="markush-run/benchmark-target500")
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--collect-workers", type=int, default=1)
    parser.add_argument("--log-root", default="markush-run/benchmark-target500/_logs")
    parser.add_argument("--skip-prior-art-download", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    manifest_path = project_root / args.manifest
    print(
        f"[watch] manifest={manifest_path} target={args.target} interval={args.interval_seconds}s",
        flush=True,
    )
    while True:
        manifest = load_manifest(manifest_path)
        records = manifest.get("records", []) if manifest else []
        metadata = manifest.get("metadata", {}) if manifest else {}
        accepted = int(metadata.get("accepted_records") or len(records))
        partial = metadata.get("partial")
        print(f"[status] records={len(records)} accepted={accepted} partial={partial}", flush=True)
        if len(records) >= args.target or accepted >= args.target:
            start_collect(args, project_root)
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
