from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collect_ep_application_candidates import KEYWORD_GROUPS


USER_AGENT = "Mozilla/5.0 (compatible; epo-report-analysis/1.0)"
APP_RE = re.compile(r"application\?number=(EP\d{8})", re.I)
EP_PUBLICATION_RE = re.compile(r"\bEP\s?(\d{7})([A-Z]\d?)?\b", re.I)
TITLE_RE = re.compile(r"<a[^>]+application\?number=EP\d{8}[^>]*>(.*?)</a>", re.I | re.S)


def request_text(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="ignore")
    if re.search(r"just a moment|__cf_chl|challenge-form|has rejected your request", text, re.I):
        raise RuntimeError("EPO returned a challenge/rejection page.")
    return text


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<.*?>", " ", str(value or "")))).strip()


def epo_advanced_search_url(query: str, publication_date: str = "") -> str:
    params = {
        "searchMode": "advanced",
        "ti": query,
        "lng": "en",
    }
    if publication_date:
        params["pd"] = publication_date
    return "https://register.epo.org/advancedSearch?" + urllib.parse.urlencode(params)


def publication_from_context(context: str) -> str:
    match = EP_PUBLICATION_RE.search(html.unescape(context))
    if not match:
        return ""
    return f"EP{match.group(1)}{match.group(2) or ''}".upper()


def title_from_context(context: str) -> str:
    match = TITLE_RE.search(context)
    if match:
        return clean_text(match.group(1))
    return ""


def parse_records(search_html: str, query: str, group: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_apps: set[str] = set()
    for match in APP_RE.finditer(search_html):
        app = match.group(1).upper()
        if app in seen_apps:
            continue
        seen_apps.add(app)
        start = max(0, match.start() - 1000)
        end = min(len(search_html), match.end() + 1500)
        context = search_html[start:end]
        publication = publication_from_context(context)
        records.append(
            {
                "application_number": app,
                "publication_number": publication,
                "title": title_from_context(context),
                "keyword_group": group["keyword_group"],
                "category": group["category"],
                "matched_queries": [query],
                "source_query": query,
                "candidate_source": "EPO Register Advanced Search",
                "epo_advanced_search_url": source_url,
                "epo_register_main_url": f"https://register.epo.org/application?number={app}&lng=en&tab=main",
                "epo_register_doclist_url": f"https://register.epo.org/application?number={app}&lng=en&tab=doclist",
                "benchmark_label": "epo_register_candidate",
            }
        )
    return records


def merge_record(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key in ["publication_number", "title", "epo_advanced_search_url"]:
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]
    queries = existing.setdefault("matched_queries", [])
    for query in incoming.get("matched_queries") or []:
        if query not in queries:
            queries.append(query)


def publication_date_buckets(start_year: int, end_year: int) -> list[str]:
    if not start_year or not end_year:
        return [""]
    return [str(year) for year in range(start_year, end_year + 1)]


def round_robin_by_group(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        by_group.setdefault(str(row.get("keyword_group") or ""), []).append(row)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(by_group.values()):
        for group in sorted(by_group):
            if by_group[group] and len(selected) < limit:
                selected.append(by_group[group].pop(0))
    return selected


def build_output(
    records: list[dict[str, Any]],
    args: argparse.Namespace,
    query_errors: list[dict[str, str]],
    partial: bool,
) -> dict[str, Any]:
    stats = {
        "total_records": len(records),
        "keyword_groups": dict(Counter(str(row.get("keyword_group") or "") for row in records)),
    }
    return {
        "metadata": {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "EPO Register Advanced Search; no Google Patents candidate discovery.",
            "partial": partial,
            "limit_requested": args.limit,
            "publication_year_from": args.publication_year_from,
            "publication_year_to": args.publication_year_to,
            "query_errors": query_errors[:200],
            "query_error_count": len(query_errors),
        },
        "stats": stats,
        "records": records,
    }


def write_output(path: Path, records: list[dict[str, Any]], args: argparse.Namespace, query_errors: list[dict[str, str]], partial: bool) -> None:
    records = round_robin_by_group(list(records), args.limit)
    records.sort(key=lambda row: (row.get("keyword_group", ""), row.get("application_number", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_output(records, args, query_errors, partial), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect EP application candidates from EPO Register Advanced Search.")
    parser.add_argument("-o", "--output", default="markush-run/benchmark/ep_application_candidates_epo_target_pool.json")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--publication-year-from", type=int, default=2014)
    parser.add_argument("--publication-year-to", type=int, default=2026)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--flush-every-query", action="store_true", default=True)
    args = parser.parse_args()

    by_app: dict[str, dict[str, Any]] = {}
    query_errors: list[dict[str, str]] = []
    buckets = publication_date_buckets(args.publication_year_from, args.publication_year_to)
    output_path = Path(args.output)
    total_queries = sum(len(group["queries"]) for group in KEYWORD_GROUPS) * len(buckets)
    query_index = 0
    for group in KEYWORD_GROUPS:
        for query in group["queries"]:
            for bucket in buckets:
                query_index += 1
                url = epo_advanced_search_url(query, bucket)
                print(
                    f"[candidate-query] {query_index}/{total_queries} group={group['keyword_group']} "
                    f"year={bucket or 'all'} query={query!r} total_candidates={len(by_app)}",
                    flush=True,
                )
                try:
                    text = request_text(url, args.timeout)
                    found = parse_records(text, query, group, url)
                    new_count = 0
                    for record in found:
                        existing = by_app.get(record["application_number"])
                        if existing:
                            merge_record(existing, record)
                        else:
                            by_app[record["application_number"]] = record
                            new_count += 1
                            print(
                                f"[candidate] #{len(by_app)} app={record['application_number']} "
                                f"pub={record.get('publication_number') or ''} group={record['keyword_group']} "
                                f"title={record.get('title') or ''}",
                                flush=True,
                            )
                    print(
                        f"[candidate-query-done] group={group['keyword_group']} year={bucket or 'all'} "
                        f"found={len(found)} new={new_count} total_candidates={len(by_app)}",
                        flush=True,
                    )
                except Exception as exc:
                    error = repr(exc)
                    query_errors.append({"query": query, "publication_date": bucket, "url": url, "error": error})
                    print(
                        f"[candidate-query-error] group={group['keyword_group']} year={bucket or 'all'} "
                        f"query={query!r} error={error}",
                        flush=True,
                    )
                if args.flush_every_query:
                    write_output(output_path, list(by_app.values()), args, query_errors, partial=True)
                if args.sleep_seconds:
                    time.sleep(args.sleep_seconds)

    records = round_robin_by_group(list(by_app.values()), args.limit)
    records.sort(key=lambda row: (row.get("keyword_group", ""), row.get("application_number", "")))
    write_output(output_path, records, args, query_errors, partial=False)
    print(f"Wrote {len(records)} records to {output_path}", flush=True)
    print(json.dumps(build_output(records, args, query_errors, partial=False)["stats"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
