from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


KEY_DOC_PATTERN = re.compile(
    r"(search_opinion|search_report|communication|annex|reply|amended_claims|claims|description|text_intended|published_international|decision|summons|grounds)",
    re.IGNORECASE,
)
PAGE_MARKER_PATTERN = re.compile(r"---\s*PAGE\s+[0-9]+\s*---", re.IGNORECASE)
PATENT_COUNTRIES = "WO|EP|US|JP|CN|GB|DE|KR|AU|ES|FR|CA"
UNVERIFIED_DIRECT_PUBLICATIONS = {"JPH03224054", "JPH03224054A"}


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


def has_meaningful_text(text: str, min_chars: int = 80) -> bool:
    cleaned = PAGE_MARKER_PATTERN.sub(" ", text)
    cleaned = re.sub(r"^\s*SOURCE:.*$", " ", cleaned, flags=re.I | re.M)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned)) >= min_chars


def meaningful_char_count(text: str) -> int:
    cleaned = PAGE_MARKER_PATTERN.sub(" ", text or "")
    cleaned = re.sub(r"^\s*SOURCE:.*$", " ", cleaned, flags=re.I | re.M)
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned))


def relative_to_case(path: Path | None, case_dir: Path) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(case_dir.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def relativize_downloaded_files(files: list[dict[str, str]], case_dir: Path) -> list[dict[str, str]]:
    relativized: list[dict[str, str]] = []
    for item in files:
        copied = dict(item)
        raw_path = copied.get("path") or ""
        if raw_path:
            copied["path"] = relative_to_case(Path(raw_path), case_dir)
        relativized.append(copied)
    return relativized


def find_main_file(case_dir: Path, application_number: str) -> Path | None:
    register_dir = case_dir / "register"
    candidates = [
        case_dir / f"{application_number}-main.html",
        case_dir / f"{application_number.replace('.', '')}-main.html",
        register_dir / f"{application_number}-main.html",
        register_dir / f"{application_number.replace('.', '')}-main.html",
    ]
    candidates.extend(case_dir.glob("*-main.html"))
    candidates.extend(register_dir.glob("*-main.html"))
    for path in candidates:
        if path.exists():
            return path
    return None


def find_doclist_file(case_dir: Path, application_number: str) -> Path | None:
    register_dir = case_dir / "register"
    candidates = [
        case_dir / f"{application_number}-doclist.csv",
        case_dir / f"{application_number.replace('.', '')}-doclist.csv",
        register_dir / f"{application_number}-doclist.csv",
        register_dir / f"{application_number.replace('.', '')}-doclist.csv",
    ]
    candidates.extend(case_dir.glob("*-doclist.csv"))
    candidates.extend(register_dir.glob("*-doclist.csv"))
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


def strip_register_bulletin(value: str) -> str:
    return norm_space(re.sub(r"\[[0-9]{4}/[0-9]{2}\]", " ", value))


def extract_register_title(main_html: str, text: str) -> str:
    match = re.search(
        r"<td[^>]*>\s*English:\s*</td>\s*<td[^>]*>(.*?)</td>",
        main_html,
        flags=re.I | re.S,
    )
    if match:
        return strip_register_bulletin(clean_text(match.group(1)))

    match = re.search(
        r"EP[0-9]+\s*-\s*(.*?)</a>",
        main_html,
        flags=re.I | re.S,
    )
    if match:
        return strip_register_bulletin(clean_text(match.group(1)))

    return strip_register_bulletin(extract_between(text, "English:", ["French:", "Entry into regional phase"], 500))


def extract_register_applicant(main_html: str, text: str) -> str:
    match = re.search(
        r"Applicant\(s\)</td>\s*<td[^>]*>(.*?)</td>",
        main_html,
        flags=re.I | re.S,
    )
    if match:
        lines = [
            strip_register_bulletin(clean_text(line))
            for line in re.split(r"<br\s*/?>", match.group(1), flags=re.I)
        ]
        lines = [
            line
            for line in lines
            if line and line.lower() != "for all designated states" and not re.fullmatch(r"\[[0-9]{4}/[0-9]{2}\]", line)
        ]
        if lines:
            return lines[0]

    applicant = extract_between(text, "Applicant(s) For all designated states", ["Inventor(s)", "Representative(s)"], 800)
    applicant = strip_register_bulletin(re.sub(r"\bFormer\b.*", "", applicant, flags=re.I))
    return applicant


def extract_meta(main_html: str, application_number: str) -> dict[str, Any]:
    text = clean_text(main_html)

    app_match = re.search(r"Application number, filing date\s+([0-9.]+)\s+([0-9]{2}\.[0-9]{2}\.[0-9]{4})", text)
    title = extract_register_title(main_html, text)
    applicant = extract_register_applicant(main_html, text)
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


def extract_register_citations_block(main_html: str) -> str:
    marker = "Documents cited:"
    start = main_html.find(marker)
    if start < 0:
        match = re.search(r">\s*Cited in\s*<", main_html, re.I)
        if not match:
            return ""
        start = main_html.rfind("<td", 0, match.start())
        if start < 0:
            start = match.start()

    tail = main_html[start:]
    end_markers = [
        r">\s*by applicant\s*<",
        "The EPO accepts no responsibility",
        '<div id="epoFooter"',
        "</body>",
    ]
    end_positions = []
    for end_marker in end_markers:
        if end_marker.startswith(">"):
            match = re.search(end_marker, tail, re.I)
            if match:
                end_positions.append(match.start())
        else:
            position = tail.find(end_marker)
            if position >= 0:
                end_positions.append(position)
    end = min(end_positions) if end_positions else len(tail)
    return tail[:end]


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
        if not has_meaningful_text(text):
            continue
        documents.append({"name": path.name, "text": text})
    return documents


def collect_case_document_texts(case_dir: Path) -> tuple[Path, list[dict[str, str]]]:
    docs_dir = case_dir / "docs"
    documents = collect_document_texts(docs_dir)
    root_documents = collect_document_texts(case_dir)
    if documents:
        seen = {doc["name"] for doc in documents}
        for doc in root_documents:
            if doc["name"] in seen:
                continue
            documents.append({"name": f"../{doc['name']}", "text": doc["text"]})
        return docs_dir, sorted(documents, key=source_document_sort_key)
    return case_dir, sorted(root_documents, key=source_document_sort_key)


def collect_downloaded_files(directory: Path) -> list[dict[str, str]]:
    if not directory.exists():
        return []

    index_path = directory / "download-index.csv"
    if index_path.exists():
        files: list[dict[str, str]] = []
        for row in read_doclist(index_path):
            path = Path(row.get("path") or "")
            if not path.is_absolute():
                path = directory / (row.get("fileName") or path.name)
            item = {
                "title": row.get("title", ""),
                "date": row.get("date", ""),
                "document_id": row.get("documentId", ""),
                "pages": row.get("pages", ""),
                "path": str(path),
                "file_name": row.get("fileName", "") or path.name,
                "source_url": row.get("url", ""),
            }
            if path.exists():
                files.append(item)
        return files

    return [
        {
            "title": path.stem,
            "date": "",
            "document_id": "",
            "pages": "",
            "path": str(path),
            "file_name": path.name,
            "source_url": "",
        }
        for path in sorted(directory.glob("*.pdf"))
    ]


def doc_row_title(row: dict[str, str]) -> str:
    return str(row.get("title") or row.get("description") or row.get("documentTitle") or row.get("name") or "")


def doc_row_date(row: dict[str, str]) -> str:
    return str(row.get("date") or row.get("Date") or row.get("created") or "")


def infer_known_outcome(
    doc_rows: list[dict[str, str]],
    documents: list[dict[str, str]],
    meta: dict[str, Any],
) -> dict[str, str]:
    evidence: list[dict[str, str]] = []
    for row in doc_rows:
        title = doc_row_title(row)
        if title:
            evidence.append({"source": title, "date": doc_row_date(row), "text": title})
    for doc in documents:
        text = norm_space(doc.get("text", "")[:5000])
        evidence.append({"source": doc.get("name", ""), "date": "", "text": f"{doc.get('name', '')} {text}"})

    checks = [
        (
            "granted",
            "yes",
            re.compile(r"decision\s+to\s+grant|decision_to_grant|is\s+hereby\s+granted|mention\s+of\s+the\s+grant", re.I),
        ),
        (
            "rejected",
            "no",
            re.compile(r"decision\s+to\s+refus|decision_to_refus|application\s+refused|is\s+refused", re.I),
        ),
        (
            "withdrawn",
            "no",
            re.compile(r"deemed\s+to\s+be\s+withdrawn|application\s+withdrawn|withdrawn", re.I),
        ),
    ]
    for outcome, grant_label, pattern in checks:
        matches = [item for item in evidence if pattern.search(item["text"])]
        if matches:
            item = sorted(matches, key=lambda x: document_date_rank(x["source"]) or document_date_rank(x["date"]), reverse=True)[0]
            return {
                "outcome": outcome,
                "grant_label": grant_label,
                "source": item["source"],
                "date": item["date"],
            }

    status = str(meta.get("register_status") or "")
    if re.search(r"patent has been granted|no opposition filed", status, re.I):
        return {"outcome": "granted", "grant_label": "yes", "source": "register_status", "date": ""}
    if re.search(r"application refused", status, re.I):
        return {"outcome": "rejected", "grant_label": "no", "source": "register_status", "date": ""}
    if re.search(r"deemed to be withdrawn", status, re.I):
        return {"outcome": "withdrawn", "grant_label": "no", "source": "register_status", "date": ""}
    if re.search(r"grant of patent is intended|examination is in progress", status, re.I):
        return {"outcome": "pending", "grant_label": "no", "source": "register_status", "date": ""}
    return {}


def document_sort_key(document: dict[str, str]) -> tuple[str, str]:
    name = document["name"]
    match = re.match(r"([0-9]{2})-([0-9]{2})-([0-9]{4})", name)
    if match:
        day, month, year = match.groups()
        return (f"{year}-{month}-{day}", name)
    match = re.match(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", name)
    if match:
        return (match.group(0), name)
    return ("0000-00-00", name)


def document_date_rank(name: str) -> int:
    match = re.match(r"(?:\.\./)?([0-9]{2})-([0-9]{2})-([0-9]{4})", name)
    if match:
        day, month, year = match.groups()
        return int(f"{year}{month}{day}")
    match = re.match(r"(?:\.\./)?([0-9]{4})-([0-9]{2})-([0-9]{2})", name)
    if match:
        year, month, day = match.groups()
        return int(f"{year}{month}{day}")
    return 0


def document_priority(name: str) -> int:
    lower = name.lower()
    if "decision_to_grant" in lower or "decision to grant" in lower:
        return 0
    if "decision_to_refus" in lower or "decision to refus" in lower or "application_refused" in lower:
        return 1
    if "withdrawn" in lower:
        return 2
    if "communication_about_intention_to_grant" in lower or "intention_to_grant" in lower:
        return 3
    if "communication_from_the_examining_division" in lower:
        return 4
    if "annex_to_the_communication" in lower:
        return 5
    if "reply_to_communication" in lower:
        return 6
    if "amended_claims" in lower:
        return 7
    if re.search(r"(?:^|_)claims(?:_|\.|$)", lower):
        return 8
    if "search_opinion" in lower or "search_report" in lower:
        return 9
    if "text_intended_for_grant" in lower:
        return 10
    if "description" in lower:
        return 11
    if "published_international" in lower:
        return 12
    return 20


def source_document_sort_key(document: dict[str, str]) -> tuple[int, int, str]:
    name = document["name"]
    return (document_priority(name), -document_date_rank(name), name)


def first_nonempty_text(documents: list[dict[str, str]], patterns: list[str]) -> tuple[str, str]:
    compiled = [re.compile(p, re.I) for p in patterns]
    for pattern in compiled:
        for doc in sorted(documents, key=document_sort_key, reverse=True):
            if pattern.search(doc["name"]):
                text = doc["text"].strip()
                if text:
                    return doc["name"], text
    return "", ""


def extract_claim_text(documents: list[dict[str, str]]) -> dict[str, Any]:
    claim_documents = [
        doc
        for doc in documents
        if not re.search(r"\btranslation\b|translations?_of_the_claims|translations?_of_claims", doc["name"], re.I)
    ]
    source, text = first_nonempty_text(
        claim_documents,
        [
            r"claims.*_ocr\.txt$",
            r"amended_claims.*_ocr\.txt$",
            r"claims.*\.txt$",
            r"amended_claims.*\.txt$",
            r"text_intended_for_grant.*(approval|clean).*_ocr\.txt$",
            r"text_intended_for_grant.*(approval|clean).*\.txt$",
            r"text_intended_for_grant",
            r"claims",
        ],
    )
    if not text or not has_meaningful_text(text, min_chars=120):
        return {"source": "", "claim_1": "", "target_claims": []}

    normalized = re.sub(r"\r\n?", "\n", text)
    claim_match = re.search(
        r"(?is)(?:claims?\s*)?(?:\n|\A)\s*1[\.)]\s+(.*?)(?:\n\s*2[\.)]\s+|\n\s*claim\s+2\b|\Z)",
        normalized,
    )
    claim_1 = norm_space(claim_match.group(1)) if claim_match else norm_space(normalized[:3000])
    if meaningful_char_count(claim_1) < 120:
        return {"source": source, "claim_1": "", "target_claims": []}
    return {
        "source": source,
        "source_type": "ocr_preview",
        "claim_1": claim_1[:5000],
        "target_claims": [{"claim_number": 1, "text": claim_1[:5000]}] if claim_1 else [],
    }


def load_verified_claim_text(case_dir: Path) -> dict[str, Any] | None:
    app = case_dir.name
    path = case_dir / f"{app}-claims-verified.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    claims = data.get("claims") or []
    if not isinstance(claims, list):
        return None
    verified_claims = [
        claim
        for claim in claims
        if isinstance(claim, dict)
        and str(claim.get("status") or "").lower() == "verified"
        and str(claim.get("text") or "").strip()
    ]
    claim_1 = next((claim for claim in verified_claims if int(claim.get("claim_number") or 0) == 1), None)
    if not claim_1:
        return None
    return {
        "source": data.get("source_pdf") or path.name,
        "source_type": "human_verified_pdf",
        "source_pdf": data.get("source_pdf") or "",
        "source_pdf_sha256": data.get("source_pdf_sha256") or "",
        "claims_verified_json": path.name,
        "claim_1": str(claim_1.get("text") or ""),
        "target_claims": [
            {
                "claim_number": claim.get("claim_number"),
                "text": claim.get("text") or "",
                "source_pages": claim.get("source_pages") or [],
            }
            for claim in sorted(verified_claims, key=lambda item: int(item.get("claim_number") or 0))
        ],
    }


def annotate_claim_review_status(case_dir: Path, claim_data: dict[str, Any]) -> dict[str, Any]:
    app = case_dir.name
    path = case_dir / f"{app}-claims-verified.json"
    if not path.exists():
        claim_data.setdefault("source_type", "ocr_preview")
        return claim_data
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        claim_data["claims_review_status"] = "parse_error"
        claim_data["claims_verified_json"] = path.name
        claim_data.setdefault("source_type", "ocr_preview")
        return claim_data
    claims = data.get("claims") or []
    verified = [
        claim
        for claim in claims
        if isinstance(claim, dict)
        and str(claim.get("status") or "").lower() == "verified"
        and str(claim.get("text") or "").strip()
    ]
    claim_data["claims_verified_json"] = path.name
    claim_data["claims_review_status"] = "verified" if claims and len(verified) == len(claims) else "needs_human_review"
    claim_data["claims_review_verified_count"] = len(verified)
    claim_data["claims_review_total_count"] = len(claims) if isinstance(claims, list) else 0
    claim_data.setdefault("source_type", "ocr_preview")
    return claim_data


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


def normalize_patent_publication(text: str) -> str:
    cleaned = norm_space(text).upper()
    cleaned = re.sub(r"^D[0-9]{1,2}\s+", "", cleaned)

    match = re.search(r"\bWO\s*(\d{4})\s*/?\s*(\d{4,7})([A-Z]\d?)?\b", cleaned)
    if match:
        year, serial, kind = match.groups()
        return f"WO{year}{serial}{kind or ''}"

    match = re.search(r"\bWO\s*(\d{2})\s*/?\s*(\d{4,6})([A-Z]\d?)?\b", cleaned)
    if match:
        year, serial, kind = match.groups()
        return f"WO{year}{serial}{kind or ''}"

    match = re.search(r"\bEP\s*((?:\d[\s,.-]?){7})([A-Z]\d?)?\b", cleaned)
    if match:
        digits = re.sub(r"\D+", "", match.group(1))
        if len(digits) == 7:
            return f"EP{digits}{match.group(2) or ''}"

    match = re.search(r"\bUS\s*(\d{4})[\s/.-]*(\d{6,7})([A-Z]\d?)?\b", cleaned)
    if match:
        year, serial, kind = match.groups()
        return f"US{year}{serial.zfill(7)}{kind or 'A1'}"

    match = re.search(r"\bUS\s*((?:\d[\s,.-]?){7,8})([A-Z]\d?)?\b", cleaned)
    if match:
        digits = re.sub(r"\D+", "", match.group(1))
        if not match.group(2) and len(digits) == 8 and int(digits) > 13000000:
            return ""
        return f"US{digits}{match.group(2) or ''}"

    match = re.search(r"\bJP\s*(0[1-9]|1[0-1])\s*((?:\d[\s,.-]?){5,6})([A-Z]\d?)?\b", cleaned)
    if match:
        year, raw_digits, kind = match.groups()
        digits = re.sub(r"\D+", "", raw_digits).lstrip("0") or "0"
        return f"JPH{year}{digits}{kind or ''}"

    match = re.search(r"\bJP\s*([A-Z])\s*((?:\d[\s,.-]?){6,9})([A-Z]\d?)?\b", cleaned)
    if match:
        era, raw_digits, kind = match.groups()
        digits = re.sub(r"\D+", "", raw_digits)
        return f"JP{era}{digits}{kind or ''}"

    match = re.search(rf"\b({PATENT_COUNTRIES})\s*((?:\d[\s,.-]?){{7,10}})([A-Z]\d?)?\b", cleaned)
    if match:
        country, raw_digits, kind = match.groups()
        digits = re.sub(r"\D+", "", raw_digits)
        return f"{country}{digits}{kind or ''}"

    return ""


def extract_patent_publications(text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    broad_pattern = re.compile(rf"\b(?:{PATENT_COUNTRIES})\s?[A-Z0-9][A-Z0-9/.,\-\s]{{3,60}}", re.I)
    for match in broad_pattern.finditer(text):
        publication = normalize_patent_publication(match.group(0))
        if publication and publication not in seen:
            seen.add(publication)
            refs.append(publication)
    return refs


def official_patent_search_link(query: str) -> str:
    publication = normalize_patent_publication(query)
    if not publication:
        return ""
    wipo_id = wipo_doc_id(publication)
    if wipo_id:
        return f"https://patentscope.wipo.int/search/en/detail.jsf?docId={quote_plus(wipo_id)}"
    return f"https://patents.google.com/patent/{publication}/en"


def wipo_doc_id(publication: str) -> str:
    publication = publication.upper()
    match = re.fullmatch(r"WO(19|20)\d{2}(\d{4,7})(?:[A-Z]\d?)?", publication)
    if match:
        return re.sub(r"(?:[A-Z]\d?)$", "", publication)
    match = re.fullmatch(r"WO(\d{2})(\d{5,6})(?:[A-Z]\d?)?", publication)
    if match:
        year, serial = match.groups()
        century = "20" if int(year) < 50 else "19"
        return f"WO{century}{year}{serial.zfill(6)}"
    return ""


def citation_query(citation: str) -> str:
    publication = normalize_patent_publication(citation)
    if publication:
        return publication
    cleaned = re.sub(r"^\s*D[0-9]{1,2}\s+", "", citation.strip(), flags=re.I)
    patent_match = re.search(r"\b((?:WO|EP|US|JP|CN|GB)[-\s]?[A-Z]?-?\s?[0-9][A-Z0-9/.,\-\s]{3,50})", cleaned, re.I)
    if patent_match:
        return norm_space(patent_match.group(1))
    return norm_space(cleaned[:120])


def citation_dedupe_key(citation: str) -> str:
    publication = normalize_patent_publication(citation)
    if publication:
        return re.sub(r"[A-Z]\d?$", "", publication).lower()
    return citation_query(citation).lower()


def infer_semantic_patent_queries(meta: dict[str, Any], claim_data: dict[str, Any], documents: list[dict[str, str]]) -> list[str]:
    seed_text = " ".join(
        str(value or "")
        for value in [
            meta.get("title"),
            claim_data.get("claim_1"),
            " ".join(doc["text"][:1400] for doc in documents[:3]),
        ]
    )
    phrases: list[str] = []
    phrase_patterns = [
        r"\b[A-Z][A-Za-z]+(?:\s+[a-z][A-Za-z]+){1,5}\s+(?:system|method|database|processor|model|estimation|distribution|storage|network)\b",
        r"\b(?:model determination|product distribution|sales estimation|geographic information system|spatial autocorrelation|multi-dimensional data storage|OLAP)\b",
    ]
    for pattern in phrase_patterns:
        for match in re.finditer(pattern, seed_text, re.I):
            phrase = norm_space(match.group(0))
            if phrase and phrase.lower() not in {p.lower() for p in phrases}:
                phrases.append(phrase)
            if len(phrases) >= 20:
                return phrases

    title = norm_space(str(meta.get("title") or ""))
    claim = norm_space(str(claim_data.get("claim_1") or ""))
    fallback_queries = [
        title,
        f"{title} patent",
        f"{title} computer implemented invention",
        "model determination system",
        "predictive model generation system",
        "candidate model final model data processing",
        "multidimensional data storage metadata layer data layer",
        "OLAP multidimensional data storage model generation",
        "computer implemented model evaluation module",
        "forecast information objective variable model generator",
        "business model generation computer system",
        "metadata layer data layer query engine",
        "networked computer multidimensional storage forecasting",
        "variable assumption model generator evaluation",
        "data warehouse model determination patent",
        "online analytical processing model generation patent",
        "computer implemented predictive analytics model patent",
        "model generator candidate model final model patent",
        "multidimensional query metadata layer patent",
        claim[:120],
    ]
    for query in fallback_queries:
        query = norm_space(query)
        if query and query.lower() not in {p.lower() for p in phrases}:
            phrases.append(query)
        if len(phrases) >= 20:
            break
    return phrases


def is_noise_citation(citation: str, application_number: str) -> bool:
    upper = citation.upper()
    if re.match(r"^D[0-9]{1,2}\s+(IS|FROM|DOES|REPRESENTS|MENTIONS|SHOWS)\b", upper):
        return True
    if not normalize_patent_publication(citation):
        return True
    app_digits = re.sub(r"\D", "", application_number)
    citation_digits = re.sub(r"\D", "", upper)
    if app_digits and (app_digits in citation_digits or app_digits[:8] in citation_digits):
        return True
    if re.search(r"\b(?:PCT/)?[A-Z]{2}\d{2}/\d{4,}\b", upper):
        return True
    noise_terms = [
        "DOCUMENTS",
        "DES BREVETS",
        "THIS ANNEX",
        "EUROPEAN SEARCH REPORT",
        "EUROPEAN SEARCH OPINION",
        "COMMUNICATION REGARDING",
        "AMENDED CLAIMS",
        "WHEREIN",
        "ACCORDING TO",
        "CHARACTERIZING",
        "SELECTED",
        "LOCATIONS AT",
        "PRODUCT IS",
        "ESTIMATING",
    ]
    return any(term in upper for term in noise_terms)


def add_prior_art(counter: Counter[str], sources: dict[str, set[str]], key: str, source: str, application_number: str) -> None:
    key = norm_space(key)
    key = re.sub(r"\s{2,}", " ", key).strip(" ;,.")
    if not key or is_noise_citation(key, application_number):
        return
    counter[key] += 1
    sources.setdefault(key, set()).add(source)


def extract_labelled_prior_art_from_lines(text: str) -> list[str]:
    lines = [norm_space(line) for line in re.split(r"\r?\n", text)]
    results: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(D[0-9]{1,2})\s*[:\-]?\s*(.*)$", line, re.I)
        if not match:
            continue
        label = match.group(1).upper()
        rest = match.group(2).strip()
        if len(rest) < 5 and index + 1 < len(lines):
            tail = []
            for next_line in lines[index + 1 : index + 4]:
                if re.match(r"^D[0-9]{1,2}\s*[:\-]?", next_line, re.I):
                    break
                if next_line:
                    tail.append(next_line)
            rest = " ".join(tail)
        publication = normalize_patent_publication(rest)
        if publication:
            results.append(f"{label} {publication}")
    return results


def make_prior_art_item(
    rank: int,
    citation: str,
    mentions: int,
    sources: list[str],
    mentioned: bool,
    retrieval_method: str,
) -> dict[str, Any]:
    publication = normalize_patent_publication(citation)
    return {
        "rank": rank,
        "citation": citation,
        "mentioned_in_examined_text": mentioned,
        "retrieval_method": retrieval_method,
        "official_source": "Patent publication direct page",
        "official_link": official_patent_search_link(publication),
        "mentions": mentions,
        "sources": sources,
    }


def is_prior_art_source(document_name: str) -> bool:
    lower = document_name.lower()
    if re.search(r"search_(?:opinion|report)|communication|annex|summons|grounds|decision", lower):
        return True
    if re.search(r"claims|description|text_intended|published_international|translation", lower):
        return False
    return True


def extract_prior_art(
    documents: list[dict[str, str]],
    rows: list[dict[str, str]],
    top_k: int,
    meta: dict[str, Any],
    claim_data: dict[str, Any],
) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    sources: dict[str, set[str]] = {}
    text_pattern = re.compile(
        r"\b(D[0-9]{1,2})\s*(?:=|:|-)?\s*("
        rf"(?:(?:{PATENT_COUNTRIES})[-\s]?[A-Z0-9][A-Z0-9/.\-\s]{{3,90}})"
        r"|(?:Chong|Chou|Isaki)[A-Za-z0-9 .,'&:/()\-\[\]]{0,180}"
        r"|Patent Abstracts[A-Za-z0-9 .,'&:/()\-\[\]]{0,180}"
        r")",
        re.I,
    )
    application_number = str(meta.get("application_number") or "")
    prior_art_documents = [doc for doc in documents if is_prior_art_source(doc["name"])]
    if not prior_art_documents:
        prior_art_documents = documents

    for doc in prior_art_documents:
        for key in extract_labelled_prior_art_from_lines(doc["text"]):
            add_prior_art(counter, sources, key, doc["name"], application_number)
        for match in text_pattern.finditer(doc["text"]):
            label = match.group(1).upper()
            publication = normalize_patent_publication(match.group(2))
            if not publication:
                continue
            key = f"{label} {publication}".strip()
            add_prior_art(counter, sources, key, doc["name"], application_number)
        for publication in extract_patent_publications(doc["text"]):
            add_prior_art(counter, sources, publication, doc["name"], application_number)

    docs: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for key, count in counter.most_common():
        if len(docs) >= top_k:
            break
        normalized_query = citation_dedupe_key(key)
        if normalized_query in seen_queries:
            continue
        publication = normalize_patent_publication(key)
        if publication in UNVERIFIED_DIRECT_PUBLICATIONS:
            continue
        item_sources = sorted(sources.get(key, []))
        docs.append(make_prior_art_item(len(docs) + 1, key, count, item_sources, bool(item_sources), "examined_text"))
        seen_queries.add(normalized_query)

    return docs


def build(case_dir: Path, application_number: str, top_k: int) -> dict[str, Any]:
    main_file = find_main_file(case_dir, application_number)
    doclist_file = find_doclist_file(case_dir, application_number)

    main_html = read_text(main_file) if main_file else ""
    doc_rows = read_doclist(doclist_file) if doclist_file else []
    docs_dir, documents = collect_case_document_texts(case_dir)
    original_application_dir = case_dir / "original-application"
    original_application_files = relativize_downloaded_files(collect_downloaded_files(original_application_dir), case_dir)
    meta = extract_meta(main_html, application_number)
    ocr_claim_data = extract_claim_text(documents)
    claim_data = load_verified_claim_text(case_dir) or annotate_claim_review_status(case_dir, ocr_claim_data)
    known_outcome = infer_known_outcome(doc_rows, documents, meta)

    prior_art_documents = list(documents)
    register_citations = extract_register_citations_block(main_html)
    if main_file and register_citations.strip():
        prior_art_documents.append({"name": f"{main_file.name}#documents_cited", "text": register_citations})

    return {
        "application_number": meta.get("application_number") or application_number,
        "benchmark_input": {
            "drug_structure": extract_drug_structure(documents, claim_data),
            "claim_text": claim_data,
            "jurisdiction": meta.get("jurisdiction", "EP"),
            "application_number": meta.get("application_number") or application_number,
            "title": meta.get("title", ""),
            "applicant": meta.get("applicant", ""),
            "filing_date": meta.get("filing_date", ""),
            "priority_date": meta.get("priority_date", ""),
            "known_outcome": known_outcome,
            "specification_data": extract_specification_data(documents),
            "prior_art_docs": extract_prior_art(prior_art_documents, doc_rows, top_k, meta, claim_data),
        },
        "source_trace": {
            "path_base": "benchmark_input_dir",
            "case_dir": ".",
            "main_html": relative_to_case(main_file, case_dir),
            "doclist_csv": relative_to_case(doclist_file, case_dir),
            "docs_dir": relative_to_case(docs_dir, case_dir),
            "original_application_dir": relative_to_case(original_application_dir, case_dir) if original_application_dir.exists() else "",
            "original_application_files": original_application_files,
            "text_documents_used": [doc["name"] for doc in documents],
            "register_status": meta.get("register_status", ""),
            "quality": {
                "source_text_documents": len(documents),
                "claim_1_meaningful_chars": meaningful_char_count(claim_data.get("claim_1") or ""),
                "source_meaningful_chars": sum(meaningful_char_count(doc.get("text", "")) for doc in documents),
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build benchmark input JSON from an EPO case directory.")
    parser.add_argument("case_dir", help="Case directory containing *-main.html, *-doclist.csv, and docs/*.txt")
    parser.add_argument("--application-number", default="", help="Application number, e.g. EP18885399")
    parser.add_argument("--top-k", type=int, default=20, help="Number of prior-art documents/snippets to keep")
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
