from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from ensure_html_field_completeness import missing_required_analysis_fields


KEY_DOC_PATTERN = re.compile(
    r"(claims|amended_claims|communication|decision|annex|reply|search_opinion|search_report|summons|grounds)",
    re.IGNORECASE,
)


def discover_case_dirs(root: Path) -> list[Path]:
    if root.name.startswith("EP") and (root / "register").exists():
        return [root]
    return sorted(path for path in root.glob("*/*") if path.is_dir() and path.name.startswith("EP"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def has_pdf_header(path: Path) -> bool:
    return path.read_bytes()[:4] == b"%PDF"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_pdf_path(directory: Path, row: dict[str, str]) -> Path:
    filename = row.get("fileName") or Path((row.get("path") or "").replace("\\", "/")).name
    return directory / filename


def validate_download_index(case_dir: Path, scope: str, require_txt: bool, require_key_ocr: bool) -> tuple[int, list[str]]:
    directory = case_dir / scope
    index_path = directory / "download-index.csv"
    issues: list[str] = []
    if not index_path.exists():
        return 0, [f"missing {scope}/download-index.csv"]
    rows = read_csv(index_path)
    if not rows:
        issues.append(f"empty {scope}/download-index.csv")
    for row in rows:
        pdf = row_pdf_path(directory, row)
        if not pdf.exists():
            issues.append(f"missing PDF: {scope}/{pdf.name}")
            continue
        if pdf.stat().st_size == 0:
            issues.append(f"empty PDF: {scope}/{pdf.name}")
        elif not has_pdf_header(pdf):
            issues.append(f"bad PDF header: {scope}/{pdf.name}")
        if require_txt:
            txt = pdf.with_suffix(".txt")
            if not txt.exists() or txt.stat().st_size == 0:
                issues.append(f"missing TXT: {scope}/{txt.name}")
        if require_key_ocr and KEY_DOC_PATTERN.search(pdf.name):
            ocr = pdf.with_name(pdf.stem + "_ocr.txt")
            if not ocr.exists() or ocr.stat().st_size <= 80:
                issues.append(f"missing key OCR: {scope}/{ocr.name}")
    return len(rows), issues


def validate_verified_claims(case_dir: Path) -> list[str]:
    app = case_dir.name
    issues: list[str] = []
    path = case_dir / f"{app}-claims-verified.json"
    if not path.exists():
        return [f"missing {path.name}"]
    try:
        data = read_json(path)
    except Exception as exc:
        return [f"claims verified JSON parse error: {exc!r}"]
    source_pdf = case_dir / str(data.get("source_pdf") or "")
    if not source_pdf.exists():
        issues.append("claims source_pdf missing")
    elif data.get("source_pdf_sha256") and sha256(source_pdf) != data.get("source_pdf_sha256"):
        issues.append("claims source_pdf sha256 mismatch")
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        issues.append("claims verified list empty")
        return issues
    seen: set[int] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            issues.append(f"claims[{index}] not object")
            continue
        try:
            number = int(claim.get("claim_number"))
        except (TypeError, ValueError):
            issues.append(f"claims[{index}].claim_number invalid")
            continue
        if number in seen:
            issues.append(f"duplicate claim {number}")
        seen.add(number)
        if str(claim.get("status") or "").lower() != "verified":
            issues.append(f"claim {number} not verified")
        text = str(claim.get("text") or "")
        if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", text)) < 20:
            issues.append(f"claim {number} text too short")
    if seen:
        expected = set(range(1, max(seen) + 1))
        missing = sorted(expected - seen)
        if 1 not in seen:
            issues.append("claim 1 missing")
        if missing:
            issues.append(f"claim numbers not contiguous, missing: {','.join(str(number) for number in missing)}")
    return issues


def validate_case(case_dir: Path, root: Path, require_verified_claims: bool = False) -> dict[str, str]:
    app = case_dir.name
    issues: list[str] = []
    register = case_dir / "register"
    required_files = [
        register / f"{app}-main.html",
        register / f"{app}-doclist.html",
        register / f"{app}-doclist.csv",
        case_dir / f"{app}-benchmark-input.json",
        case_dir / f"{app}-analysis.json",
        case_dir / f"{app}-analysis.html",
    ]
    for path in required_files:
        if not path.exists():
            issues.append(f"missing {path.relative_to(case_dir)}")
        elif path.stat().st_size == 0:
            issues.append(f"empty {path.relative_to(case_dir)}")

    docs_rows, docs_issues = validate_download_index(case_dir, "docs", require_txt=True, require_key_ocr=True)
    original_rows, original_issues = validate_download_index(case_dir, "original-application", require_txt=False, require_key_ocr=False)
    issues.extend(docs_issues)
    issues.extend(original_issues)
    if require_verified_claims:
        issues.extend(validate_verified_claims(case_dir))

    benchmark_path = case_dir / f"{app}-benchmark-input.json"
    analysis_path = case_dir / f"{app}-analysis.json"
    html_path = case_dir / f"{app}-analysis.html"
    if benchmark_path.exists():
        try:
            benchmark = read_json(benchmark_path)
            claim = ((benchmark.get("benchmark_input") or {}).get("claim_text") or {}).get("claim_1") or ""
            if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", claim)) < 120:
                issues.append("benchmark claim_1 too short")
        except Exception as exc:
            issues.append(f"benchmark JSON parse error: {exc!r}")

    if analysis_path.exists():
        try:
            analysis = read_json(analysis_path)
            issues.extend(missing_required_analysis_fields(analysis))
        except Exception as exc:
            issues.append(f"analysis JSON parse error: {exc!r}")

    if html_path.exists():
        html = html_path.read_text(encoding="utf-8", errors="ignore")
        required_html_tokens = [
            "Patent Examination Benchmark Report",
            "案件元数据",
            "审查维度评分",
            "风险原因",
            "建议依据原文",
            "引用先文",
            "说明书/支持证据",
            "审查材料证据",
        ]
        for token in required_html_tokens:
            if token not in html:
                issues.append(f"HTML missing token: {token}")

    try:
        category = str(case_dir.parent.relative_to(root))
    except ValueError:
        category = ""
    return {
        "category": category,
        "application_number": app,
        "docs_index_rows": str(docs_rows),
        "original_index_rows": str(original_rows),
        "status": "pass" if not issues else "fail",
        "issues": ";".join(issues),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate raw file completeness and final HTML/analysis field completeness for a case set.")
    parser.add_argument("root")
    parser.add_argument("-o", "--output", default="")
    parser.add_argument("--require-verified-claims", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    rows = [validate_case(case_dir, root, args.require_verified_claims) for case_dir in discover_case_dirs(root)]
    output = Path(args.output) if args.output else root / "_completeness_validation.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["category", "application_number", "docs_index_rows", "original_index_rows", "status", "issues"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote completeness validation: {output}")
    for row in rows:
        print(f"{row['application_number']}: {row['status']} docs={row['docs_index_rows']} original={row['original_index_rows']} issues={row['issues']}")
    if any(row["status"] != "pass" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
