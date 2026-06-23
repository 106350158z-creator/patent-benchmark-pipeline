from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


PAGE_MARKER_PATTERN = re.compile(r"---\s*PAGE\s+[0-9]+\s*---", re.IGNORECASE)


def meaningful_text(text: str) -> str:
    text = PAGE_MARKER_PATTERN.sub(" ", text or "")
    text = re.sub(r"^\s*SOURCE:.*$", " ", text, flags=re.I | re.M)
    return re.sub(r"\s+", " ", text).strip()


def meaningful_char_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", meaningful_text(text)))


def compact(value: str) -> str:
    return re.sub(r"\W+", "", (value or "").lower())


def discover_case_dirs(root: Path) -> list[Path]:
    if root.name.startswith("EP") and (root / "register").exists():
        return [root]
    return sorted(path for path in root.rglob("EP*") if path.is_dir() and (path / "register").exists())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def original_texts(value: Any, path: str = "") -> list[str]:
    if path.startswith("top_risk_reasons") or path.startswith("recommended_actions"):
        return []
    results: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "original_text" and isinstance(item, str):
                results.append(item)
            else:
                child_path = f"{path}.{key}" if path else str(key)
                results.extend(original_texts(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            results.extend(original_texts(item, f"{path}[{index}]"))
    return results


def doclist_outcome(case_dir: Path, app: str) -> str:
    doclist = case_dir / "register" / f"{app}-doclist.csv"
    if not doclist.exists():
        matches = list((case_dir / "register").glob("*-doclist.csv"))
        doclist = matches[0] if matches else doclist
    if not doclist.exists():
        return ""
    outcome = ""
    with doclist.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            title = (row.get("title") or "").lower()
            if "decision to grant" in title or "certificate for a european patent" in title:
                return "granted"
            if "decision to refuse" in title or "application refused" in title:
                outcome = "rejected"
            if "withdrawn" in title:
                outcome = "withdrawn"
            if not outcome and "intention to grant" in title:
                outcome = "pending_grant_intended"
    return outcome


def claim_text_chars(benchmark: dict[str, Any]) -> int:
    claim = (benchmark.get("benchmark_input") or {}).get("claim_text") or {}
    if isinstance(claim, dict):
        return meaningful_char_count(str(claim.get("claim_1") or ""))
    return meaningful_char_count(str(claim or ""))


def claims_review_stats(case_dir: Path) -> dict[str, str]:
    app = case_dir.name
    path = case_dir / f"{app}-claims-verified.json"
    if not path.exists():
        return {
            "claims_review_status": "missing",
            "claims_total": "0",
            "claims_verified": "0",
            "claims_draft": "0",
        }
    try:
        data = read_json(path)
    except Exception:
        return {
            "claims_review_status": "parse_error",
            "claims_total": "0",
            "claims_verified": "0",
            "claims_draft": "0",
        }
    claims = data.get("claims") or []
    if not isinstance(claims, list):
        claims = []
    verified = sum(
        1
        for claim in claims
        if isinstance(claim, dict)
        and str(claim.get("status") or "").lower() == "verified"
        and str(claim.get("text") or "").strip()
    )
    draft = len(claims) - verified
    status = "verified" if claims and verified == len(claims) else "needs_review" if claims else "empty"
    return {
        "claims_review_status": status,
        "claims_total": str(len(claims)),
        "claims_verified": str(verified),
        "claims_draft": str(draft),
    }


def audit_case(case_dir: Path, root: Path) -> dict[str, str]:
    app = case_dir.name
    try:
        category = str(case_dir.parent.relative_to(root)) if case_dir.parent != root else ""
    except ValueError:
        category = ""
    benchmark_path = case_dir / f"{app}-benchmark-input.json"
    analysis_path = case_dir / f"{app}-analysis.json"
    benchmark = read_json(benchmark_path) if benchmark_path.exists() else {}
    analysis = read_json(analysis_path) if analysis_path.exists() else {}
    source_trace = benchmark.get("source_trace") or {}
    docs_dir = case_dir / "docs"
    txt_files = sorted(docs_dir.glob("*.txt")) if docs_dir.exists() else []
    ocr_files = sorted(docs_dir.glob("*_ocr.txt")) if docs_dir.exists() else []
    txt_texts = [path.read_text(encoding="utf-8", errors="ignore") for path in txt_files]
    txt_sizes = [meaningful_char_count(text) for text in txt_texts]
    corpus = "\n".join(txt_texts)
    corpus_compact = compact(corpus)

    evidence = [text for text in original_texts(analysis) if meaningful_char_count(text) >= 20]
    exact_hits = 0
    compact_hits = 0
    for text in evidence:
        cleaned = meaningful_text(text)
        if cleaned and cleaned in corpus:
            exact_hits += 1
        text_compact = compact(cleaned)
        if text_compact and text_compact in corpus_compact:
            compact_hits += 1

    source_docs = source_trace.get("text_documents_used") or []
    if not isinstance(source_docs, list):
        source_docs = []
    known_outcome = (benchmark.get("benchmark_input") or {}).get("known_outcome") or {}
    if isinstance(known_outcome, dict):
        known_outcome_value = str(known_outcome.get("outcome") or "")
    else:
        known_outcome_value = str(known_outcome or "")

    issues: list[str] = []
    claim_chars = claim_text_chars(benchmark)
    claim_review = claims_review_stats(case_dir)
    total_text_chars = sum(txt_sizes)
    useful_txt = sum(1 for size in txt_sizes if size >= 300)
    if claim_chars < 120:
        issues.append("missing_claim_text")
    if not source_docs:
        issues.append("no_source_documents_used")
    if total_text_chars < 1200:
        issues.append("low_source_text")
    if analysis:
        if evidence and compact_hits / max(len(evidence), 1) < 0.6:
            issues.append("weak_evidence_match")
        inferred = doclist_outcome(case_dir, app)
        analysis_outcome = str((analysis.get("meta") or {}).get("outcome") or "")
        if inferred in {"granted", "rejected", "withdrawn"} and analysis_outcome and inferred != analysis_outcome:
            issues.append(f"outcome_mismatch_doclist_{inferred}")
    else:
        issues.append("missing_analysis")

    return {
        "category": category,
        "application_number": app,
        "claim_chars": str(claim_chars),
        **claim_review,
        "source_docs": str(len(source_docs)),
        "txt_files": str(len(txt_files)),
        "ocr_files": str(len(ocr_files)),
        "useful_txt_files": str(useful_txt),
        "docs_text_chars": str(total_text_chars),
        "analysis_outcome": str((analysis.get("meta") or {}).get("outcome") or ""),
        "grant_label": str(analysis.get("grant_label") or ""),
        "known_outcome": known_outcome_value,
        "doclist_outcome": doclist_outcome(case_dir, app),
        "aggregate_score": str(analysis.get("aggregate_score") or ""),
        "evidence_total": str(len(evidence)),
        "evidence_exact_hits": str(exact_hits),
        "evidence_compact_hits": str(compact_hits),
        "quality_status": "pass" if not issues else "needs_review",
        "issues": ";".join(issues),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit local EPO benchmark case source quality and report evidence traceability.")
    parser.add_argument("root", help="Case directory or root containing case directories.")
    parser.add_argument("-o", "--output", default="", help="CSV output path. Defaults to <root>/_quality_audit.csv.")
    args = parser.parse_args()

    root = Path(args.root)
    output = Path(args.output) if args.output else root / "_quality_audit.csv"
    rows = [audit_case(case_dir, root) for case_dir in discover_case_dirs(root)]
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "category",
        "application_number",
        "claim_chars",
        "claims_review_status",
        "claims_total",
        "claims_verified",
        "claims_draft",
        "source_docs",
        "txt_files",
        "ocr_files",
        "useful_txt_files",
        "docs_text_chars",
        "analysis_outcome",
        "grant_label",
        "known_outcome",
        "doclist_outcome",
        "aggregate_score",
        "evidence_total",
        "evidence_exact_hits",
        "evidence_compact_hits",
        "quality_status",
        "issues",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote audit CSV: {output}")
    for row in rows:
        print(
            f"{row['application_number']}: {row['quality_status']} "
            f"claim={row['claim_chars']} claims_review={row['claims_review_status']} "
            f"source_docs={row['source_docs']} "
            f"ocr={row['ocr_files']} evidence={row['evidence_compact_hits']}/{row['evidence_total']} "
            f"issues={row['issues']}"
        )


if __name__ == "__main__":
    main()
