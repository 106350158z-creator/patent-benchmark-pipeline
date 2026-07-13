import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KEYWORD_GROUPS = [
    {
        "keyword_group": "EGFR",
        "category": "oncology_target",
        "queries": ["EGFR inhibitor", "epidermal growth factor receptor inhibitor", "ErbB-1 inhibitor", "HER1 inhibitor"],
    },
    {
        "keyword_group": "PARP",
        "category": "oncology_target",
        "queries": ["PARP inhibitor", "poly ADP ribose polymerase inhibitor", "PARP1 inhibitor", "PARP2 inhibitor"],
    },
    {
        "keyword_group": "BTK",
        "category": "oncology_target",
        "queries": ["BTK inhibitor", "Bruton's tyrosine kinase inhibitor", "non-covalent BTK inhibitor", "BTK degrader PROTAC"],
    },
    {
        "keyword_group": "POLRMT",
        "category": "oncology_target",
        "queries": ["POLRMT inhibitor", "mitochondrial RNA polymerase inhibitor", "mtRNAP inhibitor", "mitochondrial transcription inhibitor"],
    },
    {"keyword_group": "JAK", "category": "oncology_target", "queries": ["JAK inhibitor", "Janus kinase inhibitor", "JAK1 inhibitor", "JAK2 inhibitor"]},
    {"keyword_group": "HDAC", "category": "oncology_target", "queries": ["HDAC inhibitor", "histone deacetylase inhibitor", "HDAC6 inhibitor", "hydroxamic acid HDAC inhibitor"]},
    {"keyword_group": "NLRP3", "category": "oncology_target", "queries": ["NLRP3 inhibitor", "inflammasome inhibitor", "NALP3 inhibitor", "Cryopyrin inhibitor"]},
    {"keyword_group": "RIPK1", "category": "oncology_target", "queries": ["RIPK1 inhibitor", "RIP1 inhibitor", "necroptosis inhibitor", "allosteric RIPK1 inhibitor"]},
    {"keyword_group": "FLT3", "category": "oncology_target", "queries": ["FLT3 inhibitor", "FMS-like tyrosine kinase 3 inhibitor", "FLT3 ITD inhibitor", "AML FLT3 inhibitor"]},
    {"keyword_group": "CYP51", "category": "antifungal", "queries": ["CYP51 inhibitor", "lanosterol 14 demethylase inhibitor", "azole antifungal", "ERG11 inhibitor"]},
    {"keyword_group": "SQLE", "category": "antifungal", "queries": ["squalene epoxidase inhibitor", "SQLE inhibitor", "terbinafine allylamine", "ergosterol biosynthesis inhibitor"]},
    {"keyword_group": "FKS", "category": "antifungal", "queries": ["beta 1,3 glucan synthase inhibitor", "FKS1 inhibitor", "echinocandin", "cell wall synthesis inhibitor"]},
    {"keyword_group": "GWT1", "category": "antifungal", "queries": ["GWT1 inhibitor", "inositol acyltransferase inhibitor", "fosmanogepix", "manogepix"]},
    {"keyword_group": "NMT", "category": "antifungal", "queries": ["N-myristoyltransferase inhibitor", "NMT inhibitor antifungal", "protein lipidation inhibitor"]},
    {"keyword_group": "DHODH", "category": "antifungal", "queries": ["DHODH inhibitor", "dihydroorotate dehydrogenase inhibitor", "olorofim", "pyrimidine biosynthesis inhibitor"]},
    {"keyword_group": "DprE1", "category": "antibacterial_tb", "queries": ["DprE1 inhibitor", "benzothiazinone DprE1", "BTZ043", "PBTZ169"]},
    {"keyword_group": "MmpL3", "category": "antibacterial_tb", "queries": ["MmpL3 inhibitor", "mycolic acid transporter inhibitor", "SQ109", "indole carboxamide tuberculosis"]},
    {"keyword_group": "QcrB", "category": "antibacterial_tb", "queries": ["QcrB inhibitor", "cytochrome bc1 inhibitor tuberculosis", "telacebec", "Q203"]},
    {"keyword_group": "LeuRS", "category": "antibacterial_tb", "queries": ["LeuRS inhibitor", "leucyl tRNA synthetase inhibitor", "benzoxaborole antibacterial", "GSK656"]},
    {"keyword_group": "GyrB", "category": "antibacterial_tb", "queries": ["GyrB inhibitor", "DNA gyrase B inhibitor", "aminobenzimidazole GyrB", "SPR719"]},
    {"keyword_group": "Pks13", "category": "antibacterial_tb", "queries": ["Pks13 inhibitor", "polyketide synthase 13 inhibitor", "Pks13 tuberculosis", "benzofuran Pks13"]},
    {"keyword_group": "InhA", "category": "antibacterial_tb", "queries": ["InhA inhibitor", "enoyl ACP reductase inhibitor", "direct InhA inhibitor", "FabI inhibitor mycobacteria"]},
    {"keyword_group": "KasA", "category": "antibacterial_tb", "queries": ["KasA inhibitor", "beta ketoacyl ACP synthase inhibitor", "FAS-II inhibitor tuberculosis"]},
    {"keyword_group": "NDH-2", "category": "antibacterial_tb", "queries": ["NDH-2 inhibitor", "type II NADH dehydrogenase inhibitor", "respiratory chain inhibitor tuberculosis"]},
    {"keyword_group": "LolCDE", "category": "antibacterial", "queries": ["LolCDE inhibitor", "lipoprotein transporter inhibitor", "LolC inhibitor", "Gram negative antibiotic LolCDE"]},
    {"keyword_group": "Lpt", "category": "antibacterial", "queries": ["LptB2FGC inhibitor", "LPS transport inhibitor", "Lpt transporter inhibitor", "Gram negative antibiotic Lpt"]},
]


def request_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
            "Accept": "text/html,application/json,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def google_xhr_url(query: str, page: int) -> str:
    params = {"q": query, "dups": "language", "country": "EP"}
    if page:
        params["page"] = str(page)
    inner = urllib.parse.urlencode(params)
    return "https://patents.google.com/xhr/query?url=" + urllib.parse.quote(inner, safe="") + "&exp="


def clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", " ", str(value or "")))).strip()


def normalize_ep_id(value: Any) -> str:
    text = str(value or "").strip().upper().split(".")[0]
    return text if text.startswith("EP") else ""


def load_excluded_ids(paths: list[str]) -> set[str]:
    excluded: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        for record in data.get("records", []):
            for key in ("application_number", "publication_number", "id"):
                value = normalize_ep_id(record.get(key))
                if value:
                    excluded.add(value)
            for value in record.get("related_publication_numbers") or []:
                normalized = normalize_ep_id(value)
                if normalized:
                    excluded.add(normalized)
    return excluded


def iter_google_results(query: str, pages: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(pages):
        data = json.loads(request_text(google_xhr_url(query, page)))
        for cluster in data.get("results", {}).get("cluster", []):
            for result in cluster.get("result", []):
                patent = result.get("patent") or {}
                publication = str(patent.get("publication_number") or "").upper().strip()
                if not re.fullmatch(r"EP\d{7}[A-Z]\d?", publication):
                    continue
                rows.append(
                    {
                        "publication_number": publication,
                        "title": clean_text(patent.get("title")),
                        "snippet": clean_text(patent.get("snippet")),
                        "assignee": clean_text(patent.get("assignee")),
                        "inventor": clean_text(patent.get("inventor")),
                        "priority_date": str(patent.get("priority_date") or ""),
                        "filing_date": str(patent.get("filing_date") or ""),
                        "publication_date": str(patent.get("publication_date") or ""),
                        "google_rank": result.get("rank"),
                        "google_patents_url": f"https://patents.google.com/patent/{publication}/en",
                        "source_query": query,
                    }
                )
    return rows


def parse_google_patent_page(publication: str) -> dict[str, str]:
    text = request_text(f"https://patents.google.com/patent/{publication}/en")
    app_match = re.search(r'itemprop="applicationNumber">\s*(EP\d{8})(?:\.\d)?[A-Z]?\s*<', text, re.I)
    status_match = re.search(r'itemprop="legalStatusIfi"[\s\S]{0,300}?itemprop="status">\s*([^<]+)<', text, re.I)
    kind_match = re.match(r"EP\d{7}([A-Z]\d?)", publication)
    return {
        "application_number": app_match.group(1).upper() if app_match else "",
        "google_legal_status": clean_text(status_match.group(1)) if status_match else "",
        "publication_kind": kind_match.group(1) if kind_match else "",
    }


def label_record(publication: str, status: str) -> str:
    kind = re.sub(r"^EP\d{7}", "", publication)
    normalized_status = status.lower()
    if kind.startswith("B"):
        return "positive_granted"
    if any(word in normalized_status for word in ["abandon", "withdraw", "ceased", "expired", "not-in-force"]):
        return "negative_ungranted_candidate"
    return "negative_ungranted_candidate"


def balanced_select(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    target_pos = limit // 2
    target_neg = limit - target_pos
    positives = [row for row in records if row["benchmark_label"] == "positive_granted"]
    negatives = [row for row in records if row["benchmark_label"] != "positive_granted"]

    def pick(pool: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        by_group: dict[str, list[dict[str, Any]]] = {}
        for row in pool:
            by_group.setdefault(row["keyword_group"], []).append(row)
        while len(selected) < target and any(by_group.values()):
            for group in sorted(by_group, key=lambda key: len(by_group[key]), reverse=True):
                if by_group[group] and len(selected) < target:
                    selected.append(by_group[group].pop(0))
        return selected

    selected = pick(positives, target_pos) + pick(negatives, target_neg)
    if len(selected) < limit:
        seen = {row["publication_number"] for row in selected}
        for row in records:
            if row["publication_number"] not in seen:
                selected.append(row)
                seen.add(row["publication_number"])
            if len(selected) >= limit:
                break
    return selected[:limit]


def round_robin_by_group(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_group.setdefault(str(row.get("keyword_group") or ""), []).append(row)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(by_group.values()):
        for group in sorted(by_group):
            if by_group[group] and len(selected) < limit:
                selected.append(by_group[group].pop(0))
    return selected


def dedupe_by_application(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_app: dict[str, dict[str, Any]] = {}
    for row in records:
        app = str(row.get("application_number") or "")
        if not app:
            continue
        existing = by_app.get(app)
        if not existing:
            by_app[app] = row
            continue
        publications = existing.setdefault("related_publication_numbers", [existing["publication_number"]])
        if row["publication_number"] not in publications:
            publications.append(row["publication_number"])
        if existing["benchmark_label"] != "positive_granted" and row["benchmark_label"] == "positive_granted":
            row["related_publication_numbers"] = publications
            by_app[app] = row
    return list(by_app.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect EP application candidates from keyword-based Google Patents searches.")
    parser.add_argument("-o", "--output", default="markush-run/benchmark/ep_application_candidates_500.json")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--pages-per-query", type=int, default=2)
    parser.add_argument("--detail-delay", type=float, default=0.2)
    parser.add_argument("--max-detail", type=int, default=1400)
    parser.add_argument("--detail-workers", type=int, default=12)
    parser.add_argument("--skip-detail", action="store_true", help="Write publication-number-only candidates without opening Google patent detail pages.")
    parser.add_argument("--exclude-source", action="append", default=[], help="Existing candidate/manifest JSON to exclude by EP application/publication number.")
    args = parser.parse_args()
    excluded_ids = load_excluded_ids(args.exclude_source)

    by_publication: dict[str, dict[str, Any]] = {}
    query_errors: list[dict[str, str]] = []
    for group in KEYWORD_GROUPS:
        for query in group["queries"]:
            try:
                for row in iter_google_results(query, args.pages_per_query):
                    existing = by_publication.setdefault(row["publication_number"], row)
                    existing.setdefault("matched_queries", [])
                    existing["matched_queries"].append(query)
                    existing["keyword_group"] = existing.get("keyword_group") or group["keyword_group"]
                    existing["category"] = existing.get("category") or group["category"]
            except Exception as exc:
                query_errors.append({"query": query, "error": repr(exc)})

    records: list[dict[str, Any]] = []
    detail_errors: list[dict[str, str]] = []

    if args.skip_detail:
        for row in round_robin_by_group(list(by_publication.values()), args.max_detail):
            kind_match = re.match(r"EP\d{7}([A-Z]\d?)", row["publication_number"])
            row["application_number"] = row["publication_number"]
            row["publication_kind"] = kind_match.group(1) if kind_match else ""
            row["google_legal_status"] = ""
            row["benchmark_label"] = label_record(row["publication_number"], "")
            row["epo_register_main_url"] = ""
            row["epo_register_doclist_url"] = ""
            records.append(row)
    else:
        rows_for_detail = round_robin_by_group(list(by_publication.values()), args.max_detail)

        def enrich(row: dict[str, Any]) -> dict[str, Any] | None:
            try:
                detail = parse_google_patent_page(row["publication_number"])
                if not detail["application_number"]:
                    return None
                row.update(detail)
                row["benchmark_label"] = label_record(row["publication_number"], row.get("google_legal_status", ""))
                row["epo_register_main_url"] = f"https://register.epo.org/application?number={row['application_number']}&lng=en&tab=main"
                row["epo_register_doclist_url"] = f"https://register.epo.org/application?number={row['application_number']}&lng=en&tab=doclist"
                return row
            except Exception as exc:
                detail_errors.append({"publication_number": row["publication_number"], "error": repr(exc)})
                return None

        with ThreadPoolExecutor(max_workers=max(1, args.detail_workers)) as executor:
            futures = []
            for row in rows_for_detail:
                futures.append(executor.submit(enrich, row))
                if args.detail_delay:
                    time.sleep(args.detail_delay)
            for future in as_completed(futures):
                enriched = future.result()
                if enriched:
                    records.append(enriched)

    if not args.skip_detail:
        records = dedupe_by_application(records)
    if excluded_ids:
        before_exclude = len(records)
        records = [
            row
            for row in records
            if normalize_ep_id(row.get("application_number")) not in excluded_ids
            and normalize_ep_id(row.get("publication_number")) not in excluded_ids
        ]
    else:
        before_exclude = len(records)
    selected = balanced_select(records, args.limit)
    selected.sort(key=lambda row: (row["benchmark_label"], row["keyword_group"], row["publication_number"]))
    label_counts = Counter(row["benchmark_label"] for row in selected)
    group_counts = Counter(row["keyword_group"] for row in selected)
    output = {
        "metadata": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "Google Patents XHR search filtered to EP publications; application numbers parsed from Google Patents publication pages.",
            "keyword_file": "文档/检索关键词.md",
            "limit_requested": args.limit,
            "pages_per_query": args.pages_per_query,
            "max_detail": args.max_detail,
            "total_unique_ep_publications_seen": len(by_publication),
            "total_with_application_number": len(records),
            "exclude_sources": args.exclude_source,
            "excluded_ids_loaded": len(excluded_ids),
            "records_before_exclude": before_exclude,
            "query_errors": query_errors,
            "detail_errors_sample": detail_errors[:50],
        },
        "stats": {
            "total_records": len(selected),
            "labels": dict(label_counts),
            "keyword_groups": dict(group_counts),
        },
        "records": selected,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(selected)} records to {path}")
    print(json.dumps(output["stats"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
