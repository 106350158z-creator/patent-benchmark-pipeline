from __future__ import annotations

import argparse
import json
import os
import re
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus


DIMENSIONS = [
    ("novelty", "新颖性", "novelty_score", "novelty_disc", "是否被单一现有技术直接公开，未被质疑通常为高分。"),
    ("inventive_step", "创造性", "inventive_step_score", "inventive_step_disc", "按 EPO problem-solution/COMVIK 判断区别特征是否贡献技术效果。"),
    ("support", "充分公开/支持", "support_score", "support_disc", "说明书是否足以实施，权利要求是否有原始公开和技术支撑。"),
    ("clarity", "清楚性", "clarity_score", "clarity_disc", "权利要求术语、边界、简洁性和 Art.84 问题。"),
    ("eligibility", "适格性", "eligibility_score", "eligibility_disc", "是否落入 EP Art.52 排除主题，是否具有进一步技术效果。"),
]
PATENT_COUNTRIES = "WO|EP|US|JP|CN|GB|DE|KR|AU|ES|FR|CA"


def h(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(h(item) for item in value)
    return escape(str(value), quote=True)


def score_class(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "score-neutral"
    if value >= 85:
        return "score-good"
    if value >= 60:
        return "score-warn"
    return "score-risk"


def pct(score: Any) -> int:
    try:
        return max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        return 0


def render_list(items: Any) -> str:
    if not items:
        return '<p class="muted">无</p>'
    rendered = []
    for item in items:
        if isinstance(item, dict):
            rendered.append(f"<li>{render_dict_inline(item)}</li>")
        else:
            rendered.append(f"<li>{h(item)}</li>")
    return "<ul>" + "".join(rendered) + "</ul>"


def render_dict_inline(item: dict[str, Any]) -> str:
    parts = []
    for key, value in item.items():
        if value in ("", None, [], {}):
            continue
        parts.append(f"<strong>{h(key)}:</strong> {h(value)}")
    return "；".join(parts)


def normalize_patent_publication(text: str) -> str:
    cleaned = re.sub(r"^Semantic official patent search:\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"^\s*D[0-9]{1,2}\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).upper()

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


def official_patent_link(citation: str) -> str:
    publication = normalize_patent_publication(citation)
    if not publication:
        return ""
    wipo_id = wipo_doc_id(publication)
    if wipo_id:
        return f"https://patentscope.wipo.int/search/en/detail.jsf?docId={quote_plus(wipo_id)}"
    return f"https://worldwide.espacenet.com/patent/search?q=pn%3D{quote_plus(publication)}"


def verified_direct_patent_link(citation: str, url: str) -> str:
    publication = normalize_patent_publication(citation)
    if not publication or not url:
        return ""
    if "patentscope.wipo.int" in url or "worldwide.espacenet.com" in url or "register.epo.org" in url:
        return url
    return ""


def patent_link_for_item(item: dict[str, Any]) -> str:
    citation = str(item.get("citation") or "")
    direct = verified_direct_patent_link(citation, str(item.get("official_link") or ""))
    if direct:
        return direct
    return official_patent_link(citation)



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


def render_external_link(url: str, label: str = "官网链接") -> str:
    if not url:
        return ""
    return f'<a href="{h(url)}" target="_blank" rel="noopener noreferrer">{h(label)}</a>'


def render_local_link(path_value: Any, current_path: Path | None, label: str | None = None) -> str:
    if not path_value:
        return ""
    raw = Path(str(path_value))
    href_text = str(label or raw.name or path_value)
    href_path = str(path_value).replace("\\", "/")
    if current_path:
        target = raw if raw.is_absolute() else (current_path.parent / raw)
        try:
            href_path = os.path.relpath(target.resolve(), current_path.parent.resolve()).replace("\\", "/")
        except OSError:
            href_path = str(path_value).replace("\\", "/")
    href = quote(href_path, safe="/:#?&=%")
    return f'<a href="{h(href)}">{h(href_text)}</a>'


def render_local_trace_item(label: str, path_value: Any, current_path: Path | None) -> str:
    if not path_value:
        return ""
    link = render_local_link(path_value, current_path)
    return f"<li><span>{h(label)}</span>{link}</li>"


def register_number(meta: dict[str, Any], benchmark: dict[str, Any] | None = None) -> str:
    value = ""
    if benchmark:
        value = str(benchmark.get("application_number") or "")
    if not value:
        value = str(meta.get("application_number") or "")
    value = value.strip()
    if re.fullmatch(r"EP\d{8}", value, flags=re.I):
        return value.upper()
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        return "EP" + digits[:8]
    return value if value.startswith("EP") else f"EP{value}" if value else ""


def load_claims_review(current_path: Path | None) -> dict[str, Any] | None:
    if not current_path:
        return None
    app = current_path.parent.name
    path = current_path.parent / f"{app}-claims-verified.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def claim_number_value(item: dict[str, Any]) -> int:
    try:
        return int(item.get("claim_number") or 0)
    except (TypeError, ValueError):
        return 0


def render_claims_block(benchmark: dict[str, Any], meta: dict[str, Any], current_path: Path | None = None) -> str:
    bench_input = benchmark.get("benchmark_input") or {}
    claim = bench_input.get("claim_text") or {}
    review = load_claims_review(current_path)
    review_claims = (review or {}).get("claims") or []
    verified_claims = [
        item
        for item in review_claims
        if isinstance(item, dict)
        and str(item.get("status") or "").lower() == "verified"
        and str(item.get("text") or "").strip()
    ]
    all_verified = bool(review_claims) and len(verified_claims) == len(review_claims)
    if all_verified:
        details = []
        for item in sorted(verified_claims, key=claim_number_value):
            number = item.get("claim_number")
            text = item.get("text") or ""
            pages = ", ".join(str(page) for page in item.get("source_pages") or [])
            details.append(
                f"<details class='claim-detail' {'open' if claim_number_value(item) == 1 else ''}>"
                f"<summary>Claim {h(number)}<span>{h('pages ' + pages if pages else '')}</span></summary>"
                f"<div class='preview-text'>{h(text)}</div>"
                "</details>"
            )
        source_pdf = render_local_link((review or {}).get("source_pdf"), current_path, "Authority PDF")
        review_json = render_local_link(f"{current_path.parent.name}-claims-verified.json" if current_path else "", current_path, "Claims JSON")
        return (
            "<h3>Verified Claims</h3>"
            f"<p class='source'>{source_pdf} {review_json}</p>"
            "<div class='claims-list'>"
            + "".join(details)
            + "</div>"
        )

    draft_claim_1 = next(
        (
            item
            for item in review_claims
            if isinstance(item, dict) and claim_number_value(item) == 1 and str(item.get("text") or "").strip()
        ),
        None,
    )
    if draft_claim_1:
        claim_text = str(draft_claim_1.get("text") or "")
        pages = ", ".join(str(page) for page in draft_claim_1.get("source_pages") or [])
        source = f"Draft claim 1 from claims review JSON{'; pages ' + pages if pages else ''}"
        claim_title = "Claim Draft Preview"
        claim_notice = "Draft OCR claim only; verify against the authority PDF before using as legal text."
    else:
        claim_text = claim.get("claim_1") or ""
        source = f"Source: {claim.get('source')}" if claim.get("source") else ""
        claim_title = "Claim OCR Preview"
        claim_notice = "OCR preview only; use the authority PDF and verified claims JSON for legal text."
    claim_html = (
        f"<div class='preview-text'>{h(claim_text)}</div><p class='source'>{h(source)}</p>"
        if claim_text
        else "<p class='muted'>No claim text extracted.</p>"
    )
    review_links = ""
    if review:
        app = current_path.parent.name if current_path else ""
        review_html = render_local_link(f"{app}-claims-review.html", current_path, "Claims review HTML")
        review_json = render_local_link(f"{app}-claims-verified.json", current_path, "Claims draft JSON")
        source_pdf = render_local_link(review.get("source_pdf"), current_path, "Authority PDF")
        total = len(review_claims)
        verified = len(verified_claims)
        review_links = (
            f"<p class='source'>Review status: {verified}/{total} verified · "
            f"{source_pdf} {review_html} {review_json}</p>"
        )
    return (
        f"<h3>{claim_title}</h3>"
        f"{claim_html}"
        f"<p class='source'>{h(claim_notice)}</p>"
        f"{review_links}"
    )


def render_bilingual_list(items: Any) -> str:
    if not items:
        return '<p class="muted">无</p>'
    rows = []
    for item in items:
        if isinstance(item, dict):
            original = str(item.get("original_text") or item.get("english") or "")
            translation = str(item.get("translation") or item.get("chinese") or "")
            text = f"{h(original)} <span class='translation-inline'>({h(translation)})</span>" if translation else h(original)
        else:
            text = h(item)
        rows.append(f"<li>{text}</li>")
    return "<ul class='bilingual-list'>" + "".join(rows) + "</ul>"


def render_source_sentence_list(items: Any, fallback_items: Any = None) -> str:
    if not items:
        return render_bilingual_list(fallback_items)
    rows = []
    for item in items:
        if not isinstance(item, dict):
            rows.append(f"<li>{h(item)}</li>")
            continue
        issue = str(item.get("issue") or "")
        source = str(item.get("source") or "")
        original = str(item.get("original_text") or "")
        translation = str(item.get("translation") or "")
        explanation = str(item.get("llm_evidence_explanation") or item.get("relevance") or "")
        rows.append(
            "<li>"
            f"<div class='source-line-meta'>{h(issue)} · {h(source)}</div>"
            f"<div class='evidence-original'>{h(original)}</div>"
            f"<div class='translation-inline'>{h(translation)}</div>"
            f"<div class='source-line-note'>{h(explanation)}</div>"
            "</li>"
        )
    return "<ul class='source-sentence-list'>" + "".join(rows) + "</ul>"


def render_disc(value: Any) -> str:
    if isinstance(value, dict):
        rows = []
        labels = {
            "analysis": "分析",
            "original_text": "原文",
            "translation": "翻译",
            "llm_evidence_explanation": "LLM证据说明",
        }
        for key in ["analysis", "original_text", "translation", "llm_evidence_explanation"]:
            if value.get(key):
                rows.append(f"<div><strong>{labels[key]}：</strong>{h(value.get(key))}</div>")
        return "".join(rows) or h(value)
    return h(value)


def disc_parts(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "analysis": str(value.get("analysis") or ""),
            "original_text": str(value.get("original_text") or ""),
            "translation": str(value.get("translation") or ""),
            "llm_evidence_explanation": str(value.get("llm_evidence_explanation") or ""),
        }
    text = "" if value is None else str(value)
    return {
        "analysis": text,
        "original_text": "",
        "translation": "",
        "llm_evidence_explanation": "",
    }


def render_meta(meta: dict[str, Any], grant_label: str, aggregate_score: Any) -> str:
    rows = [
        ("法域", meta.get("jurisdiction")),
        ("申请号", meta.get("application_number")),
        ("发明名称", meta.get("title")),
        ("申请人", meta.get("applicant")),
        ("申请日", meta.get("filing_date")),
        ("审查意见日期", meta.get("examination_date")),
        ("授权结果", meta.get("outcome") or grant_label),
    ]
    body = "".join(f"<tr><th>{h(label)}</th><td>{h(value)}</td></tr>" for label, value in rows)
    return f"""
    <section class="card" id="case-info">
      <h2>案件元数据</h2>
      <table class="kv">{body}</table>
    </section>
    """


def render_preview(benchmark: dict[str, Any] | None, meta: dict[str, Any], current_path: Path | None = None) -> str:
    benchmark = benchmark or {}
    bench_input = benchmark.get("benchmark_input") or {}
    trace = benchmark.get("source_trace") or {}
    structure = bench_input.get("drug_structure") or {}
    markush_images = structure.get("markush_images") or []
    page_images = structure.get("markush_page_images") or []
    app_no = register_number(meta, benchmark)
    links = [
        ("EPO Register Main", f"https://register.epo.org/application?number={quote_plus(app_no)}&lng=en&tab=main"),
        ("EPO Register Documents", f"https://register.epo.org/application?number={quote_plus(app_no)}&lng=en&tab=doclist"),
        ("Espacenet Search", f"https://worldwide.espacenet.com/patent/search?q={quote_plus(app_no)}"),
    ]
    local_links = []
    for label, key in [("Local main HTML", "main_html"), ("Local doclist CSV", "doclist_csv")]:
        value = trace.get(key)
        if value:
            local_links.append(render_local_trace_item(label, value, current_path))

    original_files = trace.get("original_application_files") or []
    original_links = []
    if isinstance(original_files, list):
        for item in original_files:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            title = item.get("title") or item.get("file_name") or "Original application file"
            date = item.get("date")
            pages = item.get("pages")
            suffix = " · ".join(str(v) for v in [date, f"{pages} pages" if pages else ""] if v)
            label = f"{title} ({suffix})" if suffix else str(title)
            original_links.append(f"<li>{render_local_link(item.get('path'), current_path, label)}</li>")

    image_html = ""
    display_images = markush_images
    if display_images:
        image_items = []
        for item in display_images[:6]:
            if not isinstance(item, dict) or not item.get("image_path"):
                continue
            score = item.get("score")
            score_text = f"score {h(score)} · " if score is not None else ""
            image_items.append(
                "<figure class='markush-figure'>"
                f"<img src='{h(item.get('image_path'))}' alt='Markush / Formula page image'>"
                f"<figcaption>{score_text}{h(item.get('pdf') or item.get('source'))}, page {h(item.get('page'))}</figcaption>"
                "</figure>"
            )
        if image_items:
            image_html = "<div class='markush-images'>" + "".join(image_items) + "</div>"
            page_items = []
            for item in page_images[:4]:
                if not isinstance(item, dict) or not item.get("image_path"):
                    continue
                page_items.append(
                    "<figure class='markush-figure markush-page-figure'>"
                    f"<img src='{h(item.get('image_path'))}' alt='Markush / Formula source page'>"
                    f"<figcaption>source page - {h(item.get('pdf') or item.get('source'))}, page {h(item.get('page'))}</figcaption>"
                    "</figure>"
                )
            if page_items:
                image_html += "<h4>Source Pages</h4><div class='markush-pages'>" + "".join(page_items) + "</div>"
    if not image_html:
        image_html = "<p class='muted'>No Markush / formula image selected.</p>"

    return f"""
    <section class="card" id="preview">
      <h2>Application Preview</h2>
      <div class="preview-grid">
        <div>
          {render_claims_block(benchmark, meta, current_path)}
        </div>
        <div>
          <h3>Markush / Formula</h3>
          {image_html}
          <h3>Links</h3>
          <ul class="link-list">
            {''.join(f"<li>{render_external_link(url, label)}</li>" for label, url in links)}
            {''.join(local_links)}
          </ul>
          <h3>Original Application Files</h3>
          <ul class="link-list">
            {''.join(original_links) if original_links else "<li><span class='muted'>No original application PDF downloaded.</span></li>"}
          </ul>
        </div>
      </div>
    </section>
    """


def render_dimensions(scores: dict[str, Any]) -> str:
    rows = []
    for _, label, score_key, disc_key, description in DIMENSIONS:
        score = scores.get(score_key, "")
        parts = disc_parts(scores.get(disc_key, "未被质疑"))
        rows.append(
            "<tr>"
            "<td>"
            f"<div class='dim-score-box {score_class(score)}'>"
            f"<div class='dim-box-name'>{h(label)}</div>"
            f"<div class='dim-box-score'>{h(score)}</div>"
            "</div>"
            "</td>"
            f"<td class='dim-desc'>{h(description)}</td>"
            f"<td>{h(parts['analysis'])}</td>"
            f"<td><div class='evidence-original'>{h(parts['original_text'])}</div></td>"
            f"<td>{h(parts['translation'])}</td>"
            f"<td>{h(parts['llm_evidence_explanation'])}</td>"
            "</tr>"
        )
    return f"""
    <section class="card" id="dimensions">
      <h2>审查维度评分</h2>
      <div class="table-scroll">
        <table class="dimension-table">
          <thead><tr><th>维度/分数</th><th>字段描述</th><th>分析</th><th>原文</th><th>翻译</th><th>LLM证据说明</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </section>
    """


def render_prior_art_link(item: dict[str, Any], current_path: Path | None) -> str:
    local_pdf = item.get("local_pdf") or item.get("pdf_path")
    if local_pdf:
        return render_local_link(local_pdf, current_path, "Local PDF")
    link = patent_link_for_item(item)
    if link:
        return render_external_link(link, "Document link")
    return "<span class='muted tiny'>No verified patent link</span>"


def render_prior_art(prior_art: Any, current_path: Path | None = None) -> str:
    if not prior_art:
        return '<p class="muted">无</p>'
    if not any(isinstance(item, dict) for item in prior_art):
        items = []
        for item in prior_art[:20]:
            citation = str(item)
            link = official_patent_link(citation)
            link_html = render_external_link(link, "Document link") if link else "<span class='muted tiny'>No verified patent link</span>"
            items.append(
                "<li>"
                f"{h(citation)} "
                f"{link_html}"
                "</li>"
            )
        return "<ol class='prior-list'>" + "".join(items) + "</ol>"

    rows = []
    for index, item in enumerate(prior_art[:20], start=1):
        if not isinstance(item, dict):
            citation = str(item)
            mentioned = ""
            explanation = ""
            method = ""
        else:
            citation = str(item.get("citation") or "")
            method = str(item.get("retrieval_method") or "")
            if item.get("mentioned_in_examined_text"):
                mentioned = "Examined text"
            elif method in {"official_semantic_retrieval", "google_patents_semantic_retrieval"}:
                mentioned = "Official supplemental query"
            else:
                mentioned = "Supplemental source"
            explanation = str(item.get("llm_evidence_explanation") or item.get("relevance") or "")
        link_html = render_prior_art_link(item if isinstance(item, dict) else {"citation": citation}, current_path)
        tag_class = "tag-hit" if mentioned == "Examined text" else "tag-semantic"
        rows.append(
            "<tr>"
            f"<td>{h(index)}</td>"
            f"<td>{h(citation)}<div class='small-link'>{link_html}</div></td>"
            f"<td><span class='tag {tag_class}'>{h(mentioned)}</span><div class='muted tiny'>{h(method)}</div></td>"
            f"<td>{h(explanation)}</td>"
            "</tr>"
        )
    return (
        "<table>"
        "<thead><tr><th>序号</th><th>引用/相关专利</th><th>来源标记</th><th>LLM证据说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def render_evidence_table(items: Any, fallback_location_label: str = "位置") -> str:
    rows = []
    for item in items or []:
        if isinstance(item, dict):
            location = item.get("location") or item.get("source") or ""
            issue = item.get("issue") or ""
            original = item.get("original_text") or ""
            translation = item.get("translation") or ""
            explanation = item.get("llm_evidence_explanation") or item.get("relevance") or ""
            if not (original or translation or explanation) and item:
                explanation = render_dict_inline(item)
            rows.append(
                "<tr>"
                f"<td>{h(issue)}</td>"
                f"<td>{h(location)}</td>"
                f"<td><div class='evidence-original'>{h(original)}</div></td>"
                f"<td>{h(translation)}</td>"
                f"<td>{h(explanation)}</td>"
                "</tr>"
            )
        else:
            rows.append(f"<tr><td></td><td>{h(fallback_location_label)}</td><td colspan='3'>{h(item)}</td></tr>")

    if not rows:
        rows.append("<tr><td colspan='5' class='muted'>无</td></tr>")
    return (
        "<table>"
        "<thead><tr><th>议题</th><th>位置/来源</th><th>原文</th><th>翻译</th><th>LLM证据说明</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def render_evidence(evidence: dict[str, Any], current_path: Path | None = None) -> str:
    return f"""
    <section class="card" id="evidence">
      <h2>证据链</h2>
      <table class="kv">
        <tr><th>受影响权利要求</th><td>{h(evidence.get('affected_claims'))}</td></tr>
        <tr><th>审查轮次</th><td>{h(evidence.get('examination_rounds'))}</td></tr>
      </table>
      <h3>引用先文 / 官网相关专利</h3>
      {render_prior_art(evidence.get('prior_art_documents'), current_path)}
      <h3>说明书/支持证据</h3>
      {render_evidence_table(evidence.get('specification_support'))}
      <h3>审查材料证据</h3>
      {render_evidence_table(evidence.get('examination_material_evidence'))}
    </section>
    """


def merge_benchmark_prior_art(evidence: dict[str, Any], benchmark: dict[str, Any] | None) -> dict[str, Any]:
    if not benchmark:
        return evidence
    benchmark_prior_art = ((benchmark.get("benchmark_input") or {}).get("prior_art_docs") or [])
    if not benchmark_prior_art:
        return evidence

    explanation_by_publication: dict[str, str] = {}
    for item in evidence.get("prior_art_documents") or []:
        if not isinstance(item, dict):
            continue
        publication = normalize_patent_publication(str(item.get("citation") or ""))
        explanation = str(item.get("llm_evidence_explanation") or item.get("relevance") or "")
        if publication and explanation:
            explanation_by_publication.setdefault(publication, explanation)

    merged = []
    for index, item in enumerate(benchmark_prior_art[:20], start=1):
        if not isinstance(item, dict):
            continue
        citation = str(item.get("citation") or "")
        publication = normalize_patent_publication(citation)
        if not publication:
            continue
        merged.append(
            {
                "rank": item.get("rank") or index,
                "citation": citation,
                "mentioned_in_examined_text": bool(item.get("mentioned_in_examined_text")),
                "retrieval_method": item.get("retrieval_method", ""),
                "official_source": item.get("official_source", ""),
                "official_link": patent_link_for_item(item),
                "publication_number": item.get("publication_number") or publication,
                "local_pdf": item.get("local_pdf", ""),
                "pdf_download_url": item.get("pdf_download_url", ""),
                "pdf_lookup_url": item.get("pdf_lookup_url", ""),
                "pdf_download_status": item.get("pdf_download_status", ""),
                "pdf_sha256": item.get("pdf_sha256", ""),
                "pdf_bytes": item.get("pdf_bytes", ""),
                "llm_evidence_explanation": explanation_by_publication.get(
                    publication,
                    "Extracted from verified patent publication references in the benchmark input.",
                ),
            }
        )

    if not merged:
        return evidence
    merged_evidence = dict(evidence)
    merged_evidence["prior_art_documents"] = merged
    return merged_evidence


def render_report_nav(current_path: Path | None = None) -> str:
    previous_link = '<span class="nav-disabled">上一个 HTML</span>'
    next_link = '<span class="nav-disabled">下一个 HTML</span>'
    if current_path:
        current_resolved = current_path.resolve()
        html_paths = sorted(
            current_path.parent.parent.glob("*/*-analysis.html"),
            key=lambda path: path.as_posix().lower(),
        )
        current_index = next(
            (index for index, html_path in enumerate(html_paths) if html_path.resolve() == current_resolved),
            None,
        )
        if current_index is not None:
            if current_index > 0:
                previous_path = html_paths[current_index - 1]
                previous_rel = Path("..") / previous_path.parent.name / previous_path.name
                previous_link = f"<a href='{h(previous_rel.as_posix())}'>上一个 HTML：{h(previous_path.parent.name)}</a>"
            if current_index < len(html_paths) - 1:
                next_path = html_paths[current_index + 1]
                next_rel = Path("..") / next_path.parent.name / next_path.name
                next_link = f"<a href='{h(next_rel.as_posix())}'>下一个 HTML：{h(next_path.parent.name)}</a>"
    return f"""
    <nav class="jump-nav">
      <a href="#preview">Preview</a>
      <a href="#case-info">Case</a>
      <a href="#dimensions">Dimensions</a>
      <a href="#actions">Actions</a>
      <a href="#evidence">Evidence</a>
      {previous_link}
      {next_link}
    </nav>
    """


def render_html(data: dict[str, Any], source_name: str, benchmark: dict[str, Any] | None = None, current_path: Path | None = None) -> str:
    meta = data.get("meta") or {}
    title = "Patent Examination Benchmark Report"
    app_no = meta.get("application_number") or ""
    aggregate = data.get("aggregate_score", "")
    actions = data.get("action_basis_source_sentences") or []
    fallback_actions = data.get("recommended_actions") or []
    evidence = merge_benchmark_prior_art(data.get("evidence_trace") or {}, benchmark)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{h(title)}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --card: #ffffff;
      --text: #1d2433;
      --muted: #657084;
      --line: #d9dee8;
      --good: #15803d;
      --good-bg: #dcfce7;
      --warn: #a16207;
      --warn-bg: #fef3c7;
      --risk: #b91c1c;
      --risk-bg: #fee2e2;
      --accent: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.6;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    header {{
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.25;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 20px;
    }}
    h3 {{
      margin: 20px 0 10px;
      font-size: 16px;
    }}
    .subtitle {{
      color: var(--muted);
      margin: 0;
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(220px, 300px) 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
      margin-bottom: 16px;
    }}
    .jump-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 16px;
    }}
    .jump-nav a,
    .jump-nav .nav-disabled {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 7px 10px;
      font-size: 13px;
      font-weight: 650;
    }}
    .jump-nav .nav-disabled {{
      color: var(--muted);
      background: #f1f3f6;
      cursor: not-allowed;
    }}
    .preview-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(280px, 0.85fr);
      gap: 18px;
    }}
    .preview-text {{
      max-height: 260px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      padding: 12px;
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 13px;
      line-height: 1.55;
    }}
    .preview-text.compact {{
      max-height: 140px;
      margin-top: 6px;
    }}
    .claims-list {{
      display: grid;
      gap: 10px;
    }}
    .claim-detail {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #f8fafc;
    }}
    .claim-detail summary {{
      cursor: pointer;
      font-weight: 700;
    }}
    .claim-detail summary span {{
      margin-left: 8px;
      color: var(--muted);
      font-weight: 400;
      font-size: 12px;
    }}
    .markush-images {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }}
    .markush-pages {{
      display: grid;
      gap: 12px;
      margin: 8px 0 12px;
    }}
    .markush-figure {{
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }}
    .markush-figure img {{
      display: block;
      width: 100%;
      max-height: 460px;
      object-fit: contain;
      background: #f8fafc;
    }}
    .markush-page-figure img {{
      max-height: 760px;
    }}
    .markush-figure figcaption {{
      border-top: 1px solid var(--line);
      padding: 7px 10px;
      color: var(--muted);
      font-size: 12px;
    }}
    .candidate-gallery {{
      margin: 10px 0 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }}
    .candidate-gallery summary {{
      cursor: pointer;
      padding: 9px 11px;
      font-weight: 650;
      background: #f8fafc;
    }}
    .candidate-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 8px;
      padding: 10px;
    }}
    .candidate-figure {{
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fff;
    }}
    .candidate-figure img {{
      display: block;
      width: 100%;
      height: 130px;
      object-fit: contain;
      background: #f8fafc;
    }}
    .candidate-figure figcaption {{
      border-top: 1px solid var(--line);
      padding: 5px 7px;
      color: var(--muted);
      font-size: 11px;
    }}
    .link-list {{
      display: grid;
      gap: 6px;
      padding-left: 18px;
    }}
    .link-list code {{
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      word-break: break-all;
    }}
    .score-card {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 170px;
    }}
    .label {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 8px;
    }}
    .score-note {{
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .big-score {{
      font-size: 56px;
      font-weight: 750;
      line-height: 1;
    }}
    .score-bar {{
      height: 10px;
      background: #e5e7eb;
      border-radius: 999px;
      overflow: hidden;
      margin-top: 18px;
    }}
    .score-bar span {{
      display: block;
      height: 100%;
      background: currentColor;
    }}
    .score-good {{ color: var(--good); }}
    .score-warn {{ color: var(--warn); }}
    .score-risk {{ color: var(--risk); }}
    .score-neutral {{ color: var(--accent); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    .table-scroll {{
      overflow-x: auto;
    }}
    .dimension-table {{
      min-width: 1500px;
    }}
    .dimension-table th:nth-child(1),
    .dimension-table td:nth-child(1) {{
      width: 150px;
    }}
    .dimension-table th:nth-child(2),
    .dimension-table td:nth-child(2) {{
      width: 220px;
    }}
    .dimension-table th:nth-child(3),
    .dimension-table td:nth-child(3) {{
      width: 320px;
    }}
    .dimension-table th:nth-child(4),
    .dimension-table td:nth-child(4),
    .dimension-table th:nth-child(5),
    .dimension-table td:nth-child(5) {{
      width: 340px;
    }}
    .dimension-table th:nth-child(6),
    .dimension-table td:nth-child(6) {{
      width: 260px;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 10px 12px;
      vertical-align: top;
      text-align: left;
      word-break: break-word;
    }}
    th {{
      background: #f8fafc;
      font-weight: 650;
    }}
    .kv th {{
      width: 180px;
    }}
    .dim-name {{
      width: 130px;
      font-weight: 650;
    }}
    .dim-desc {{
      color: var(--muted);
      font-size: 14px;
    }}
    .dim-score-box {{
      border: 1px solid currentColor;
      border-radius: 8px;
      padding: 10px;
      background: #f8fafc;
      display: grid;
      gap: 6px;
      justify-items: start;
      min-height: 82px;
    }}
    .dim-box-name {{
      color: var(--text);
      font-weight: 700;
      font-size: 15px;
    }}
    .dim-box-score {{
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
    }}
    .pill {{
      display: inline-flex;
      min-width: 48px;
      justify-content: center;
      border-radius: 999px;
      padding: 3px 10px;
      font-weight: 700;
      background: #eef2ff;
    }}
    .pill.score-good {{ background: var(--good-bg); }}
    .pill.score-warn {{ background: var(--warn-bg); }}
    .pill.score-risk {{ background: var(--risk-bg); }}
    ul {{
      margin: 0;
      padding-left: 20px;
    }}
    .bilingual-list {{
      display: grid;
      gap: 8px;
    }}
    .source-sentence-list {{
      display: grid;
      gap: 12px;
    }}
    .source-sentence-list li {{
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .source-sentence-list li:last-child {{
      border-bottom: 0;
      padding-bottom: 0;
    }}
    .source-line-meta,
    .source-line-note {{
      color: var(--muted);
      font-size: 13px;
    }}
    .source-line-meta {{
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .source-line-note {{
      margin-top: 4px;
    }}
    .translation-inline {{
      color: var(--muted);
    }}
    .columns {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }}
    .muted {{
      color: var(--muted);
    }}
    .tiny {{
      font-size: 12px;
      margin-top: 4px;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .small-link {{
      margin-top: 4px;
      font-size: 13px;
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .tag-hit {{
      color: #166534;
      background: #dcfce7;
    }}
    .tag-semantic {{
      color: #1d4ed8;
      background: #dbeafe;
    }}
    .evidence-original {{
      white-space: pre-wrap;
    }}
    .source {{
      margin-top: 8px;
      font-size: 13px;
      color: var(--muted);
    }}
    @media (max-width: 820px) {{
      .grid, .columns, .preview-grid {{
        grid-template-columns: 1fr;
      }}
      main {{
        padding: 20px 12px 32px;
      }}
      .kv th {{
        width: 130px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{h(title)}</h1>
      <p class="subtitle">{h(app_no)} · {h(meta.get('title') or source_name)}</p>
      <p class="source">来源 JSON：{h(source_name)}</p>
    </header>

    {render_report_nav(current_path)}
    {render_preview(benchmark, meta, current_path)}
    {render_meta(meta, data.get("grant_label", ""), aggregate)}
    {render_dimensions(data.get("dimension_scores") or {})}

    <section class="card" id="actions">
      <h2>建议依据原文</h2>
      {render_source_sentence_list(actions, fallback_actions)}
    </section>

    {render_evidence(evidence, current_path)}
  </main>
</body>
</html>
"""


def find_benchmark_input(input_path: Path, explicit_path: Path | None = None) -> dict[str, Any] | None:
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    candidates.extend(sorted(input_path.parent.glob("*benchmark-input.json")))
    for candidate in candidates:
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8-sig"))
    return None


def convert(input_path: Path, output_path: Path | None, benchmark_input_path: Path | None = None) -> Path:
    data = json.loads(input_path.read_text(encoding="utf-8-sig"))
    benchmark = find_benchmark_input(input_path, benchmark_input_path)
    if output_path is None:
        output_path = input_path.with_suffix(".html")
    html = render_html(data, input_path.name, benchmark, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8", newline="\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert patent analysis JSON to a standalone HTML report.")
    parser.add_argument("input_json", help="Path to the final patent analysis JSON.")
    parser.add_argument("-o", "--output", help="Output HTML path. Defaults to input path with .html suffix.")
    parser.add_argument("--benchmark-input", help="Optional benchmark input JSON for claim/Markush/link preview.")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output) if args.output else None
    benchmark_input_path = Path(args.benchmark_input) if args.benchmark_input else None
    result = convert(input_path, output_path, benchmark_input_path)
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()
