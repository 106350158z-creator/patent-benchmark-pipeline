import argparse
import csv
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


KEY_DOC_PATTERN = re.compile(
    r"(search_opinion|search_report|communication|annex|reply|amended_claims|claims|description|text_intended|published_international)",
    re.IGNORECASE,
)


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def norm_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return ""
    return text[:limit] if limit else text


def read_doclist(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def find_main_file(case_dir: Path, application_number: str) -> Path | None:
    candidates = [
        case_dir / f"{application_number}-main.html",
        case_dir / f"{application_number.replace('.', '')}-main.html",
    ]
    candidates.extend(case_dir.glob("*-main.html"))
    for path in candidates:
        if path.exists():
            return path
    return None


def find_doclist_file(case_dir: Path, application_number: str) -> Path | None:
    candidates = [
        case_dir / f"{application_number}-doclist.csv",
        case_dir / f"{application_number.replace('.', '')}-doclist.csv",
    ]
    candidates.extend(case_dir.glob("*-doclist.csv"))
    for path in candidates:
        if path.exists():
            return path
    return None


def extract_between(text: str, start: str, end_candidates: list[str], max_len: int = 900) -> str:
    idx = text.find(start)
    if idx < 0:
        return ""
    tail = text[idx + len(start):]
    end_positions = [tail.find(end) for end in end_candidates if tail.find(end) >= 0]
    end = min(end_positions) if end_positions else max_len
    return norm_space(tail[: min(end, max_len)])


def extract_meta(main_html: str, application_number: str) -> dict[str, Any]:
    text = clean_text(main_html)

    app_match = re.search(r"Application number, filing date\s+([0-9.]+)\s+([0-9]{2}\.[0-9]{2}\.[0-9]{4})", text)
    title = extract_between(text, "English:", ["French:", "Entry into regional phase"], 500)
    applicant = extract_between(text, "Applicant(s) For all designated states", ["Inventor(s)", "Representative(s)"], 800)
    priority_match = re.search(r"Priority number, date\s+.*?([0-9]{2}\.[0-9]{2}\.[0-9]{4})", text)

    status = ""
    for marker in [
        "No opposition filed within time limit",
        "The patent has been granted",
        "Application deemed to be withdrawn",
        "Application refused",
        "Examination is in progress",
        "Grant of patent is intended",
    ]:
        if marker in text:
            status = marker
            break

    filing_date = app_match.group(2) if app_match else ""
    priority_date = priority_match.group(1) if priority_match else ""

    return {
        "jurisdiction": "EP",
        "application_number": f"EP{app_match.group(1)}" if app_match else application_number,
        "title": title,
        "applicant": applicant,
        "filing_date": normalize_ep_date(filing_date),
        "priority_date": normalize_ep_date(priority_date),
        "register_status": status,
    }


def normalize_ep_date(value: str) -> str:
    match = re.fullmatch(r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})", value.strip())
    if not match:
        return value
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def collect_document_texts(docs_dir: Path) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    if not docs_dir.exists():
        return documents
    for path in sorted(docs_dir.glob("*.txt")):
        if not KEY_DOC_PATTERN.search(path.name):
            continue
        text = read_text(path)
        if not text.strip():
            continue
        documents.append({"name": path.name, "text": text})
    return documents


def first_nonempty_text(documents: list[dict[str, str]], patterns: list[str]) -> tuple[str, str]:
    compiled = [re.compile(p, re.I) for p in patterns]
    for doc in documents:
        if any(p.search(doc["name"]) for p in compiled):
            text = doc["text"].strip()
            if text:
                return doc["name"], text
    return "", ""


def extract_claim_text(documents: list[dict[str, str]]) -> dict[str, Any]:
    source, text = first_nonempty_text(
        documents,
        [
            r"text_intended_for_grant.*(approval|clean).*(_ocr|_text)",
            r"amended_claims.*(_ocr|_text)",
            r"claims.*(_ocr|_text)",
        ],
    )
    if not text:
        return {"source": "", "claim_1": "", "target_claims": []}

    normalized = re.sub(r"\r\n?", "\n", text)
    claim_match = re.search(
        r"(?is)(?:claims?\s*)?(?:\n|\A)\s*1[\.)]\s+(.*?)(?:\n\s*2[\.)]\s+|\n\s*claim\s+2\b|\Z)",
        normalized,
    )
    claim_1 = norm_space(claim_match.group(1)) if claim_match else norm_space(normalized[:3000])
    return {
        "source": source,
        "claim_1": claim_1[:5000],
        "target_claims": [{"claim_number": 1, "text": claim_1[:5000]}] if claim_1 else [],
    }


def extract_drug_structure(documents: list[dict[str, str]], claim_data: dict[str, Any]) -> dict[str, Any]:
    snippets: list[dict[str, str]] = []
    smiles: list[str] = []
    pattern = re.compile(r"(.{0,160}(?:Formula|Markush|SMILES|structure of Formula|compound of Formula).{0,360})", re.I | re.S)
    smiles_pattern = re.compile(r"\bSMILES[:：]\s*([A-Za-z0-9@+\-\[\]\(\)=#$\\/%.]+)")

    for doc in documents:
        for match in pattern.finditer(doc["text"]):
            snippets.append({"source": doc["name"], "text": norm_space(match.group(1))[:700]})
            if len(snippets) >= 8:
                break
        for match in smiles_pattern.finditer(doc["text"]):
            smiles.append(match.group(1))
        if len(snippets) >= 8:
            break

    claim_text = claim_data.get("claim_1") or ""
    if claim_text and re.search(r"\bFormula\b|compound", claim_text, re.I):
        snippets.insert(0, {"source": claim_data.get("source", "claim_text"), "text": claim_text[:900]})

    return {
        "markush_or_formula_snippets": snippets[:8],
        "smiles": sorted(set(smiles)),
        "extraction_note": "自动抽取 Formula/Markush/SMILES 邻近文本；化学结构图像仍需人工或专用结构识别工具确认。",
    }


def snippets_by_keywords(documents: list[dict[str, str]], keywords: list[str], max_items: int = 8) -> list[dict[str, str]]:
    regex = re.compile(r"(.{0,180}(?:" + "|".join(re.escape(k) for k in keywords) + r").{0,420})", re.I | re.S)
    results: list[dict[str, str]] = []
    seen = set()
    for doc in documents:
        for match in regex.finditer(doc["text"]):
            snippet = norm_space(match.group(1))[:800]
            key = (doc["name"], snippet[:120])
            if key in seen:
                continue
            seen.add(key)
            results.append({"source": doc["name"], "text": snippet})
            if len(results) >= max_items:
                return results
    return results


def extract_specification_data(documents: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "examples": snippets_by_keywords(documents, ["Example", "Examples", "实施例"], 8),
        "pharmacology_or_effect_data": snippets_by_keywords(
            documents,
            ["MIC", "viability", "cell toxicity", "antifungal", "fungal", "activity", "药效", "毒性", "活性"],
            10,
        ),
        "synthesis_routes": snippets_by_keywords(
            documents,
            ["synthesis", "prepared", "preparation", "route", "scheme", "合成", "制备"],
            8,
        ),
        "use_descriptions": snippets_by_keywords(
            documents,
            ["treat", "treatment", "infection", "cryptococcosis", "Candida", "Aspergillus", "用途"],
            8,
        ),
        "comparative_data": snippets_by_keywords(
            documents,
            ["comparative", "closest prior art", "D3", "D5", "93%", "77%", "对比"],
            8,
        ),
    }


def extract_prior_art(documents: list[dict[str, str]], rows: list[dict[str, str]], top_k: int) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    sources: dict[str, set[str]] = {}
    text_pattern = re.compile(
        r"\b(D[0-9]{1,2})\s*(?:=|:)?\s*((?:WO|EP|US|JP|CN)\s?[0-9][A-Z0-9/.\-\s]*|[A-Z][A-Za-z0-9 .,'&:/()\-\[\]]{8,160})",
        re.I,
    )

    for doc in documents:
        for match in text_pattern.finditer(doc["text"]):
            label = match.group(1).upper()
            citation = norm_space(match.group(2))
            citation = re.sub(r"\s{2,}", " ", citation)
            key = f"{label} {citation}".strip()
            counter[key] += 1
            sources.setdefault(key, set()).add(doc["name"])

    for row in rows:
        title = row.get("title", "")
        if re.search(r"search report|search opinion", title, re.I):
            key = title
            counter[key] += 1
            sources.setdefault(key, set()).add("doclist.csv")

    docs = []
    for rank, (key, count) in enumerate(counter.most_common(top_k), start=1):
        docs.append({"rank": rank, "citation": key, "mentions": count, "sources": sorted(sources.get(key, []))})
    return docs


def build(case_dir: Path, application_number: str, top_k: int) -> dict[str, Any]:
    main_file = find_main_file(case_dir, application_number)
    doclist_file = find_doclist_file(case_dir, application_number)
    docs_dir = case_dir / "docs"

    main_html = read_text(main_file) if main_file else ""
    doc_rows = read_doclist(doclist_file) if doclist_file else []
    documents = collect_document_texts(docs_dir)
    meta = extract_meta(main_html, application_number)
    claim_data = extract_claim_text(documents)

    return {
        "application_number": meta.get("application_number") or application_number,
        "benchmark_input": {
            "drug_structure": extract_drug_structure(documents, claim_data),
            "claim_text": claim_data,
            "jurisdiction": meta.get("jurisdiction", "EP"),
            "filing_date": meta.get("filing_date", ""),
            "priority_date": meta.get("priority_date", ""),
            "specification_data": extract_specification_data(documents),
            "prior_art_docs": extract_prior_art(documents, doc_rows, top_k),
        },
        "source_trace": {
            "case_dir": str(case_dir),
            "main_html": str(main_file) if main_file else "",
            "doclist_csv": str(doclist_file) if doclist_file else "",
            "docs_dir": str(docs_dir),
            "text_documents_used": [doc["name"] for doc in documents],
            "register_status": meta.get("register_status", ""),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build benchmark input JSON from an EPO case directory.")
    parser.add_argument("case_dir", help="Case directory containing *-main.html, *-doclist.csv, and docs/*.txt")
    parser.add_argument("--application-number", default="", help="Application number, e.g. EP18885399")
    parser.add_argument("--top-k", type=int, default=10, help="Number of prior-art documents/snippets to keep")
    parser.add_argument("-o", "--output", default="", help="Output JSON path")
    args = parser.parse_args()

    case_dir = Path(args.case_dir)
    application_number = args.application_number or case_dir.name.split("_")[0]
    result = build(case_dir, application_number, args.top_k)
    output = Path(args.output) if args.output else case_dir / f"{application_number}_benchmark_input.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()

