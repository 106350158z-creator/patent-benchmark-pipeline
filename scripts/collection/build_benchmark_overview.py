from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


CASE_ID_RE = re.compile(r"^EP\d{8,}$", re.IGNORECASE)
SKIP_CASE_PATH_PARTS = {"assets"}
def project_root() -> Path:
    return next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "README.md").exists() and (parent / "scripts").exists()
    )


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def normalize_application(value: Any) -> str:
    application = str(value or "").strip().upper().split(".")[0]
    if application and not application.startswith("EP"):
        application = f"EP{application}"
    return application


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def default_manifests(root: Path) -> list[Path]:
    benchmark_dir = root / "markush-run" / "benchmark"
    candidates = [
        path
        for path in benchmark_dir.glob("ep_review_file_sources_full_*.json")
        if re.fullmatch(r"ep_review_file_sources_full_\d{8}[a-z]*", path.stem)
    ]
    if candidates:
        return [max(candidates, key=lambda path: path.stat().st_mtime)]
    merged = benchmark_dir / "ep_review_file_sources_merged_current.json"
    return [merged] if merged.exists() else []


def load_target_records(manifests: list[Path], root: Path) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        if not manifest.exists():
            raise FileNotFoundError(f"Target manifest not found: {manifest}")
        payload = read_json(manifest)
        for source in payload.get("records", []):
            application = normalize_application(
                source.get("application_number") or source.get("id") or source.get("publication_number")
            )
            if not application:
                continue
            target = targets.setdefault(
                application,
                {
                    "application_number": application,
                    "publication_numbers": [],
                    "title": "",
                    "assignee": "",
                    "keyword_groups": [],
                    "benchmark_labels": [],
                    "target_manifests": [],
                },
            )
            publication = str(source.get("publication_number") or "").strip().upper()
            if publication and publication not in target["publication_numbers"]:
                target["publication_numbers"].append(publication)
            if not target["title"] and source.get("title"):
                target["title"] = str(source["title"])
            if not target["assignee"] and source.get("assignee"):
                target["assignee"] = str(source["assignee"])
            for field, output_field in (("keyword_group", "keyword_groups"), ("benchmark_label", "benchmark_labels")):
                value = str(source.get(field) or "").strip()
                if value and value not in target[output_field]:
                    target[output_field].append(value)
            manifest_path = relative_path(manifest, root)
            if manifest_path not in target["target_manifests"]:
                target["target_manifests"].append(manifest_path)
    return targets


def discover_case_files(scan_roots: list[Path]) -> Iterator[tuple[Path, Path]]:
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        result = subprocess.run(
            [
                "rg",
                "--files",
                "--no-ignore",
                "-g",
                "!**/_state/**",
                "-g",
                "!**/_logs/**",
                "-g",
                "!**/.git/**",
                "-g",
                "!**/__pycache__/**",
                str(scan_root),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        for raw_path in result.stdout.splitlines():
            path = Path(raw_path)
            parts = path.parts
            case_index = next((index for index, part in enumerate(parts) if CASE_ID_RE.fullmatch(part)), None)
            if case_index is None:
                continue
            case_dir = Path(parts[0], *parts[1 : case_index + 1])
            relative_parts = {part.lower() for part in parts[case_index + 1 :]}
            # Markush candidate images are local derived working files, not fetched
            # patent material. Listing hundreds of thousands of them would make the
            # inventory unusable, so the overview focuses on acquired artifacts and
            # final per-case outputs.
            if relative_parts & SKIP_CASE_PATH_PARTS:
                continue
            yield case_dir, path


def artifact_kind(relative_file: Path) -> str:
    parts = relative_file.parts
    lowered = {part.lower() for part in parts}
    suffix = relative_file.suffix.lower()
    if "original-application" in lowered:
        if suffix == ".pdf":
            return "original_publication_pdf"
        if suffix == ".txt":
            return "original_publication_text"
        if suffix in {".html", ".xml", ".zip"}:
            return "original_publication_fallback"
        return "original_publication_metadata"
    if "docs" in lowered:
        if suffix == ".pdf":
            return "file_wrapper_pdf"
        if suffix == ".txt":
            return "file_wrapper_text"
        return "file_wrapper_metadata"
    if "register" in lowered:
        return "register_metadata"
    if "prior-art" in lowered:
        return "prior_art"
    if suffix == ".pdf":
        return "unclassified_pdf"
    if suffix in {".html", ".csv", ".json"}:
        return "case_metadata"
    return "other"


def build_location(case_dir: Path, paths: list[Path], root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    kinds: Counter[str] = Counter()
    for path in sorted(paths):
        rel = path.relative_to(case_dir)
        kind = artifact_kind(rel)
        kinds[kind] += 1
        files.append(
            {
                "path": relative_path(path, root),
                "relative_path": rel.as_posix(),
                "kind": kind,
            }
        )
    return {
        "case_path": relative_path(case_dir, root),
        "file_count": len(files),
        "artifact_counts": dict(sorted(kinds.items())),
        "files": files,
    }


def build_overview(root: Path, manifests: list[Path], scan_roots: list[Path]) -> dict[str, Any]:
    targets = load_target_records(manifests, root)
    locations_by_app: dict[str, list[dict[str, Any]]] = {}
    paths_by_case: dict[Path, list[Path]] = {}
    for case_dir, path in discover_case_files(scan_roots):
        paths_by_case.setdefault(case_dir, []).append(path)
    for case_dir, paths in paths_by_case.items():
        application = normalize_application(case_dir.name)
        locations_by_app.setdefault(application, []).append(build_location(case_dir, paths, root))

    applications = sorted(set(targets) | set(locations_by_app))
    records: list[dict[str, Any]] = []
    file_kinds: Counter[str] = Counter()
    total_files = 0
    for application in applications:
        target = targets.get(application, {})
        locations = sorted(locations_by_app.get(application, []), key=lambda item: item["case_path"])
        for location in locations:
            total_files += int(location["file_count"])
            file_kinds.update(location["artifact_counts"])
        records.append(
            {
                "application_number": application,
                "in_target_benchmark": application in targets,
                "publication_numbers": target.get("publication_numbers", []),
                "title": target.get("title", ""),
                "assignee": target.get("assignee", ""),
                "keyword_groups": target.get("keyword_groups", []),
                "benchmark_labels": target.get("benchmark_labels", []),
                "target_manifests": target.get("target_manifests", []),
                "capture_location_count": len(locations),
                "captured_file_count": sum(int(location["file_count"]) for location in locations),
                "capture_locations": locations,
            }
        )

    target_records = [record for record in records if record["in_target_benchmark"]]
    return {
        "metadata": {
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "project_root": str(root),
            "target_manifests": [relative_path(path, root) for path in manifests],
            "scan_roots": [relative_path(path, root) for path in scan_roots],
            "excluded_case_subdirectories": sorted(SKIP_CASE_PATH_PARTS),
            "update_model": "Rebuilt from target manifests and on-disk case directories. Entries are keyed by application number and file path, so reruns do not duplicate entries and removed files disappear.",
        },
        "summary": {
            "target_patent_count": len(targets),
            "captured_patent_count": len(locations_by_app),
            "target_patents_with_files": sum(1 for record in target_records if record["captured_file_count"]),
            "captured_only_patent_count": sum(1 for record in records if not record["in_target_benchmark"]),
            "captured_file_count": total_files,
            "captured_file_kinds": dict(sorted(file_kinds.items())),
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the canonical benchmark overview: target EP applications plus every captured file path."
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="Target benchmark manifest relative to the project root. Repeat for multiple target sets.",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        default=[],
        help="Directory to scan for EP case folders, relative to the project root. Defaults to markush-run.",
    )
    parser.add_argument("--output", default="markush-run/benchmark/benchmark-overview.json")
    args = parser.parse_args()

    root = project_root()
    manifests = [root / path for path in args.manifest] if args.manifest else default_manifests(root)
    if not manifests:
        raise SystemExit("No target manifest found. Pass --manifest explicitly.")
    scan_roots = [root / path for path in args.scan_root] if args.scan_root else [root / "markush-run"]
    overview = build_overview(root, manifests, scan_roots)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(overview, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote overview: {output}")
    print(json.dumps(overview["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
