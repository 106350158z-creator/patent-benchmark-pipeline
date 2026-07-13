from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import fitz


PAGE_MARKER = re.compile(r"---\s*PAGE\s+([0-9]+)\s*---", re.I)
CLAIM_START = re.compile(r"^\s*([0-9]{1,3})\s*[\.)]\s*(.*)$")
CLAIMS_HEADING = re.compile(r"^\s*(?:amended\s+)?claims?(?:\s*\([^)]+\))?\s*$", re.I)
SECTION_AFTER_CLAIMS = re.compile(r"^\s*(?:abstract|drawings?|description|references?)\s*$", re.I)


def discover_case_dirs(root: Path) -> list[Path]:
    if root.name.startswith("EP") and (root / "docs").exists():
        return [root]
    return sorted(path for path in root.rglob("EP*") if path.is_dir() and (path / "docs").exists())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def meaningful_char_count(text: str) -> int:
    text = PAGE_MARKER.sub(" ", text or "")
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", text))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def date_key(path: Path) -> int:
    match = re.match(r"([0-9]{2})-([0-9]{2})-([0-9]{4})", path.name)
    if not match:
        return 0
    day, month, year = match.groups()
    return int(f"{year}{month}{day}")


def doclist_outcome(case_dir: Path) -> str:
    csvs = sorted((case_dir / "register").glob("*-doclist.csv"))
    rows = read_csv(csvs[0]) if csvs else []
    outcome = ""
    for row in rows:
        title = (row.get("title") or "").lower()
        if "decision to grant" in title or "certificate for a european patent" in title:
            return "granted"
        if "decision to refuse" in title or "application refused" in title:
            outcome = "rejected"
        if "withdrawn" in title:
            outcome = "withdrawn"
        if not outcome and "intention to grant" in title:
            outcome = "pending"
    return outcome


def benchmark_outcome(case_dir: Path) -> str:
    app = case_dir.name
    path = case_dir / f"{app}-benchmark-input.json"
    if not path.exists():
        return doclist_outcome(case_dir)
    try:
        known = ((read_json(path).get("benchmark_input") or {}).get("known_outcome") or {})
        if isinstance(known, dict) and known.get("outcome"):
            return str(known.get("outcome"))
    except Exception:
        pass
    return doclist_outcome(case_dir)


def classify_pdf(path: Path) -> str:
    lower = path.name.lower()
    if "text_intended_for_grant" in lower and "clean_copy" in lower:
        return "text_intended_for_grant_clean_copy"
    if "text_intended_for_grant" in lower and "version_for_approval" in lower:
        return "text_intended_for_grant_version_for_approval"
    if "amended_claims" in lower:
        return "amended_claims"
    if re.search(r"(?:^|_)claims(?:_|\.|$)", lower):
        return "claims"
    return "other"


def authority_priority(path: Path, outcome: str) -> int:
    kind = classify_pdf(path)
    if outcome == "granted":
        order = {
            "text_intended_for_grant_clean_copy": 0,
            "text_intended_for_grant_version_for_approval": 1,
            "claims": 2,
            "amended_claims": 3,
        }
    else:
        order = {
            "claims": 0,
            "amended_claims": 1,
            "text_intended_for_grant_clean_copy": 2,
            "text_intended_for_grant_version_for_approval": 3,
        }
    return order.get(kind, 20)


def candidate_pdfs(case_dir: Path) -> list[Path]:
    docs_dir = case_dir / "docs"
    return sorted(
        [
            path
            for path in docs_dir.glob("*.pdf")
            if classify_pdf(path) != "other" and "translation" not in path.name.lower()
        ],
        key=lambda path: (authority_priority(path, benchmark_outcome(case_dir)), -date_key(path), path.name),
    )


def text_source_for_pdf(pdf: Path) -> tuple[Path | None, str]:
    candidates = [pdf.with_name(pdf.stem + "_ocr.txt"), pdf.with_suffix(".txt")]
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        if meaningful_char_count(text) >= 120:
            return path, text
    return None, ""


def select_sources(case_dir: Path) -> tuple[Path, str, Path | None, str]:
    outcome = benchmark_outcome(case_dir)
    pdfs = candidate_pdfs(case_dir)
    if not pdfs:
        raise RuntimeError(f"No candidate claims PDF found: {case_dir}")
    authority = pdfs[0]
    authority_kind = classify_pdf(authority)
    text_path, text = text_source_for_pdf(authority)
    if text_path:
        return authority, authority_kind, text_path, text
    for pdf in pdfs[1:]:
        text_path, text = text_source_for_pdf(pdf)
        if text_path:
            return authority, authority_kind, text_path, text
    return authority, authority_kind, None, ""


def clean_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line.replace("\u00a0", " ")).strip()
    return line


def is_noise_line(line: str) -> bool:
    cleaned = clean_line(line)
    if not cleaned:
        return True
    if re.fullmatch(r"[0-9]{1,3}", cleaned):
        return True
    if CLAIMS_HEADING.match(cleaned):
        return True
    if cleaned.lower() in {"claim", "claimset"}:
        return True
    if re.fullmatch(r"[0-9]{1,3}\s*(?:->|→|-|/)\s*[0-9]{1,3}", cleaned):
        return True
    if re.fullmatch(r"EP\s*[0-9][0-9\s.]{6,}", cleaned, re.I):
        return True
    return False


def claim_start_match(line: str) -> re.Match[str] | None:
    match = CLAIM_START.match(line)
    if not match:
        return None
    number = int(match.group(1))
    if number < 1:
        return None
    return match


def locate_claims_section(text: str) -> str:
    """Return the claims section when a heading can be found; otherwise return the original text."""
    raw_lines = text.splitlines()
    cleaned = [clean_line(line) for line in raw_lines]
    start = 0
    for index, line in enumerate(cleaned):
        if not CLAIMS_HEADING.match(line):
            continue
        window = cleaned[index + 1 : min(len(cleaned), index + 40)]
        if any((match := claim_start_match(candidate)) and int(match.group(1)) == 1 for candidate in window):
            start = index
            break

    if start == 0:
        for index, line in enumerate(cleaned):
            match = claim_start_match(line)
            if match and int(match.group(1)) == 1:
                start = index
                break

    for index in range(start, -1, -1):
        if PAGE_MARKER.search(raw_lines[index]):
            start = index
            break

    end = len(raw_lines)
    seen_claim = False
    for index in range(start, len(cleaned)):
        if claim_start_match(cleaned[index]):
            seen_claim = True
        elif seen_claim and SECTION_AFTER_CLAIMS.match(cleaned[index]):
            end = index
            break
    return "\n".join(raw_lines[start:end])


def split_claims(text: str) -> list[dict[str, Any]]:
    text = locate_claims_section(text)
    claims: list[dict[str, Any]] = []
    current_number: int | None = None
    current_lines: list[str] = []
    current_pages: set[int] = set()
    preamble_lines: list[str] = []
    preamble_pages: set[int] = set()
    page = 1

    def flush() -> None:
        nonlocal current_number, current_lines, current_pages
        if current_number is None:
            return
        body = "\n".join(line for line in current_lines if line).strip()
        if meaningful_char_count(body) >= 20:
            claims.append(
                {
                    "claim_number": current_number,
                    "text": body,
                    "source_pages": sorted(current_pages) or [page],
                    "status": "draft",
                    "reviewer": "",
                    "reviewed_at": "",
                    "notes": "",
                }
            )
        current_number = None
        current_lines = []
        current_pages = set()

    for raw_line in text.splitlines():
        page_match = PAGE_MARKER.search(raw_line)
        if page_match:
            page = int(page_match.group(1))
            continue
        line = clean_line(raw_line)
        if is_noise_line(line):
            continue
        match = claim_start_match(line)
        if match:
            number = int(match.group(1))
            rest = clean_line(match.group(2))
            if current_number is None and number > 1 and meaningful_char_count("\n".join(preamble_lines)) >= 80:
                claims.append(
                    {
                        "claim_number": 1,
                        "text": "\n".join(preamble_lines).strip(),
                        "source_pages": sorted(preamble_pages) or [1],
                        "status": "draft",
                        "reviewer": "",
                        "reviewed_at": "",
                        "notes": "Draft claim 1 inferred from text before the first numbered dependent claim.",
                    }
                )
                preamble_lines = []
                preamble_pages = set()
            flush()
            current_number = number
            current_pages = {page}
            current_lines = [rest] if rest else []
            continue
        if current_number is None:
            preamble_lines.append(line)
            preamble_pages.add(page)
        else:
            current_lines.append(line)
            current_pages.add(page)
    flush()

    if not claims and meaningful_char_count("\n".join(preamble_lines)) >= 80:
        claims.append(
            {
                "claim_number": 1,
                "text": "\n".join(preamble_lines).strip(),
                "source_pages": sorted(preamble_pages) or [1],
                "status": "draft",
                "reviewer": "",
                "reviewed_at": "",
                "notes": "Draft claim 1 inferred from OCR text without numbered claim boundaries.",
            }
        )

    deduped: dict[int, dict[str, Any]] = {}
    for claim in claims:
        number = int(claim["claim_number"])
        if number not in deduped or meaningful_char_count(claim["text"]) > meaningful_char_count(deduped[number]["text"]):
            deduped[number] = claim
    if deduped:
        highest = max(deduped)
        if highest <= 200:
            for number in range(1, highest + 1):
                deduped.setdefault(
                    number,
                    {
                        "claim_number": number,
                        "text": "",
                        "source_pages": [],
                        "status": "draft",
                        "reviewer": "",
                        "reviewed_at": "",
                        "notes": "Draft placeholder: OCR did not expose a clear boundary for this claim number. Fill from the authority PDF during review.",
                    },
                )
    return [deduped[number] for number in sorted(deduped)]


def relative(path: Path | None, case_dir: Path) -> str:
    if not path:
        return ""
    try:
        return path.resolve().relative_to(case_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def clear_review_images(image_dir: Path) -> None:
    if not image_dir.exists():
        return
    for path in image_dir.glob("*.png"):
        path.unlink()


def render_pages(pdf: Path, pages: list[int], case_dir: Path, image_dir: Path, zoom: float, overwrite: bool) -> dict[int, str]:
    image_dir.mkdir(parents=True, exist_ok=True)
    page_links: dict[int, str] = {}
    with fitz.open(pdf) as doc:
        for page_number in pages:
            if page_number < 1 or page_number > doc.page_count:
                continue
            out = image_dir / f"{pdf.stem}_p{page_number:03d}.png"
            if overwrite or not out.exists():
                pix = doc.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                pix.save(out)
            page_links[page_number] = relative(out, case_dir)
    return page_links


def href(path: str) -> str:
    return quote(path, safe="/:#?&=%")


def render_review_html(data: dict[str, Any], case_dir: Path, page_links: dict[int, str]) -> str:
    claims = data.get("claims") or []
    source_pdf = str(data.get("source_pdf") or "")
    draft_source = str(data.get("draft_text_source") or "")
    warnings = data.get("draft_warnings") or []
    warning_html = ""
    if warnings:
        warning_html = "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in warnings) + "</ul>"
    rows = []
    for claim in claims:
        pages = claim.get("source_pages") or []
        page_html = " ".join(
            f"<a href='#{escape('page-' + str(page))}'>p.{page}</a>" for page in pages if page in page_links
        )
        rows.append(
            "<details class='claim'{}>".format(" open" if claim.get("claim_number") == 1 else "")
            + f"<summary>Claim {escape(str(claim.get('claim_number')))} · {escape(str(claim.get('status')))} · {page_html}</summary>"
            + f"<textarea spellcheck='false'>{escape(str(claim.get('text') or ''))}</textarea>"
            + f"<p class='muted'>notes: {escape(str(claim.get('notes') or ''))}</p>"
            + "</details>"
        )
    page_sections = []
    for page, path in sorted(page_links.items()):
        page_sections.append(
            f"<section id='page-{page}' class='page'><h3>Page {page}</h3><img src='{href(path)}' alt='PDF page {page}'></section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(str(data.get('application_number')))} Claims Review</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background: #f6f7f9; color: #1d2433; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px 18px 48px; }}
    .panel, .claim, .page {{ background: #fff; border: 1px solid #d9dee8; border-radius: 8px; padding: 16px; margin: 0 0 14px; }}
    .muted {{ color: #657084; }}
    a {{ color: #1d4ed8; text-decoration: none; }}
    textarea {{ width: 100%; min-height: 180px; margin-top: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; line-height: 1.45; }}
    img {{ width: 100%; height: auto; border: 1px solid #d9dee8; }}
    .grid {{ display: grid; grid-template-columns: minmax(320px, 0.9fr) 1.1fr; gap: 16px; align-items: start; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>{escape(str(data.get('application_number')))} Claims Review</h1>
  <section class="panel">
    <p><strong>Authority PDF:</strong> <a href="{href(source_pdf)}">{escape(source_pdf)}</a></p>
    <p><strong>Draft OCR source:</strong> <a href="{href(draft_source)}">{escape(draft_source)}</a></p>
    <p class="muted">All generated claims start as draft. Edit the JSON file after checking against the authority PDF, then set each reviewed claim status to verified.</p>
    {warning_html}
  </section>
  <div class="grid">
    <section>
      {''.join(rows) if rows else '<p class="panel muted">No draft claims were extracted.</p>'}
    </section>
    <section>
      {''.join(page_sections) if page_sections else '<p class="panel muted">No page images rendered.</p>'}
    </section>
  </div>
</main>
</body>
</html>
"""


def render_existing_package(case_dir: Path, json_path: Path, html_path: Path, zoom: float, overwrite_images: bool, clear_images: bool) -> tuple[Path, Path]:
    data = read_json(json_path)
    source_pdf = case_dir / str(data.get("source_pdf") or "")
    claims = data.get("claims") or []
    page_set: set[int] = set()
    for claim in claims:
        for raw_page in claim.get("source_pages", []):
            try:
                page_set.add(int(raw_page))
            except (TypeError, ValueError):
                continue
    pages = sorted(page_set)[:80]
    image_dir = case_dir / "assets" / "claim-review"
    if clear_images:
        clear_review_images(image_dir)
    page_links = render_pages(source_pdf, pages, case_dir, image_dir, zoom, overwrite_images) if source_pdf.exists() and pages else {}
    html_path.write_text(render_review_html(data, case_dir, page_links), encoding="utf-8")
    return json_path, html_path


def build_claims_package(case_dir: Path, zoom: float, overwrite_images: bool, overwrite_json: bool, clear_images: bool) -> tuple[Path, Path]:
    app = case_dir.name
    json_path = case_dir / f"{app}-claims-verified.json"
    html_path = case_dir / f"{app}-claims-review.html"
    if json_path.exists() and not overwrite_json:
        return render_existing_package(case_dir, json_path, html_path, zoom, overwrite_images, clear_images)

    authority_pdf, source_kind, draft_text_path, draft_text = select_sources(case_dir)
    claims = split_claims(draft_text) if draft_text else []
    draft_pdf = draft_text_path.with_name(re.sub(r"_ocr\.txt$", ".pdf", draft_text_path.name)) if draft_text_path else None
    if draft_pdf and not draft_pdf.exists():
        draft_pdf = draft_text_path.with_suffix(".pdf")
    image_pdf = authority_pdf
    pages = sorted({page for claim in claims for page in claim.get("source_pages", [])})[:80]
    image_dir = case_dir / "assets" / "claim-review"
    if clear_images:
        clear_review_images(image_dir)
    page_links = render_pages(image_pdf, pages, case_dir, image_dir, zoom, overwrite_images) if pages else {}
    now = datetime.now(timezone.utc).isoformat()
    empty_claims = [claim["claim_number"] for claim in claims if not str(claim.get("text") or "").strip()]
    draft_warnings = [
        f"Claim {number} is an OCR placeholder and must be filled from the authority PDF during review."
        for number in empty_claims
    ]
    data = {
        "application_number": app,
        "source_pdf": relative(authority_pdf, case_dir),
        "source_pdf_sha256": file_sha256(authority_pdf),
        "source_kind": source_kind,
        "draft_text_source": relative(draft_text_path, case_dir),
        "draft_text_pdf": relative(draft_pdf, case_dir),
        "draft_generated_at": now,
        "draft_status": "needs_human_review",
        "draft_warnings": draft_warnings,
        "claims": claims,
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_review_html(data, case_dir, page_links), encoding="utf-8")
    return json_path, html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate draft legal-claim review JSON and HTML from local EPO claim PDFs.")
    parser.add_argument("root", help="Case directory or nested root containing EP case directories.")
    parser.add_argument("--zoom", type=float, default=1.8)
    parser.add_argument("--overwrite-images", action="store_true")
    parser.add_argument("--clear-images", action="store_true")
    parser.add_argument("--overwrite-json", action="store_true", help="Overwrite an existing claims verified JSON. Leave off after human review.")
    args = parser.parse_args()

    root = Path(args.root)
    for case_dir in discover_case_dirs(root):
        json_path, html_path = build_claims_package(case_dir, args.zoom, args.overwrite_images, args.overwrite_json, args.clear_images)
        print(f"{case_dir.name}: wrote {json_path} and {html_path}")


if __name__ == "__main__":
    main()
