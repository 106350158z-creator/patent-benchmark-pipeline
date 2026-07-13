from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def safe_name(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", name)
    value = re.sub(r"\s+", "_", value)
    return value[:120]


def valid_pdf(path: Path) -> bool:
    try:
        return path.exists() and path.read_bytes()[:4] == b"%PDF"
    except OSError:
        return False


def date_key(value: str) -> str:
    match = re.match(r"^([0-9]{2})\.([0-9]{2})\.([0-9]{4})$", value or "")
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return value or ""


def title_key(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def output_name(row: dict[str, str]) -> str:
    date = (row.get("date") or "").replace(".", "-")
    title = safe_name(row.get("title") or "")
    return f"{date}_{title}_{row.get('documentId') or ''}.pdf"


def document_url(row: dict[str, str]) -> str:
    app = row.get("applicationNumber") or ""
    if app and not app.startswith("EP"):
        app = f"EP{app}"
    return (
        "https://register.epo.org/application?"
        f"documentId={row.get('documentId') or ''}&appnumber={app}&showPdfPage=all&proc="
    )


def select_rows(rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, str]]:
    selected = rows
    if not args.download_all:
        pattern = re.compile(args.title_regex, re.IGNORECASE)
        selected = [row for row in selected if pattern.search(row.get("title") or "")]
    if args.earliest_per_title:
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in selected:
            grouped.setdefault(title_key(row.get("title") or ""), []).append(row)
        selected = [
            sorted(group, key=lambda row: (date_key(row.get("date") or ""), row.get("documentId") or ""))[0]
            for group in grouped.values()
        ]
    return sorted(selected, key=lambda row: (date_key(row.get("date") or ""), row.get("title") or ""))


def manifest_row(row: dict[str, str], out: Path, url: str) -> dict[str, str]:
    app = row.get("applicationNumber") or ""
    if app and not app.startswith("EP"):
        app = f"EP{app}"
    return {
        "applicationNumber": app,
        "documentId": row.get("documentId") or "",
        "date": row.get("date") or "",
        "title": row.get("title") or "",
        "phase": row.get("phase") or "",
        "pages": row.get("pages") or "",
        "fileName": out.name,
        "path": str(out),
        "url": url,
    }


def looks_like_challenge(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in ("just a moment", "cloudflare", "__cf_chl", "robotabuse", "challenge"))


def fetch_pdf(page, url: str, timeout_ms: int, manual_wait_seconds: int) -> bytes:
    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    if response is None:
        raise RuntimeError("No browser response")
    body = response.body()
    if body.startswith(b"%PDF"):
        return body
    content_type = (response.headers.get("content-type") or "").lower()
    text = ""
    try:
        text = body[:4096].decode("utf-8", errors="ignore")
    except Exception:
        text = ""
    if manual_wait_seconds > 0 and (response.status in {403, 429, 503} or looks_like_challenge(text)):
        deadline = time.time() + manual_wait_seconds
        while time.time() < deadline:
            time.sleep(3)
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response is not None:
                body = response.body()
                if body.startswith(b"%PDF"):
                    return body
    raise RuntimeError(f"Browser response is not PDF: status={response.status} content-type={content_type}")


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download selected EPO Register PDFs through a persistent browser profile.")
    parser.add_argument("--doclist-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title-regex", default="Communication|Annex|Decision|search report|search opinion|Summons|Minutes|intention to grant")
    parser.add_argument("--download-all", action="store_true")
    parser.add_argument("--earliest-per-title", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--proxy-server", default="")
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--start-minimized", action="store_true")
    parser.add_argument("--retry-count", type=int, default=1)
    parser.add_argument("--retry-delay-seconds", type=float, default=2.0)
    parser.add_argument("--request-delay-milliseconds", type=int, default=800)
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--manual-wait-seconds", type=int, default=30)
    args = parser.parse_args()

    doclist_csv = Path(args.doclist_csv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(doclist_csv.open("r", encoding="utf-8-sig", newline="")))
    rows = select_rows(rows, args)
    if not rows:
        raise RuntimeError(f"No documents matched TitleRegex: {args.title_regex}")

    manifest: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    fieldnames = ["applicationNumber", "documentId", "date", "title", "phase", "pages", "fileName", "path", "url"]
    failure_fields = fieldnames + ["error"]
    timeout_ms = max(1, args.request_timeout_seconds) * 1000

    launch_args = ["--disable-blink-features=AutomationControlled"]
    if args.start_minimized:
        launch_args.append("--start-minimized")

    with sync_playwright() as p:
        context_kwargs = {
            "headless": args.headless,
            "accept_downloads": True,
            "args": launch_args,
        }
        if args.proxy_server:
            context_kwargs["proxy"] = {"server": args.proxy_server}
        if args.browser_channel:
            context_kwargs["channel"] = args.browser_channel
        context = p.chromium.launch_persistent_context(str(Path(args.profile_dir)), **context_kwargs)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if args.start_minimized:
                try:
                    page.set_viewport_size({"width": 1, "height": 1})
                except Exception:
                    pass
            for row in rows:
                out = output_dir / output_name(row)
                url = document_url(row)
                if valid_pdf(out):
                    print(f"Using cached PDF {row.get('date')} - {row.get('title')}", flush=True)
                    manifest.append(manifest_row(row, out, url))
                    continue
                print(f"Browser downloading {row.get('date')} - {row.get('title')}", flush=True)
                error = ""
                for attempt in range(1, max(1, args.retry_count) + 1):
                    try:
                        body = fetch_pdf(page, url, timeout_ms, args.manual_wait_seconds)
                        out.write_bytes(body)
                        if not valid_pdf(out):
                            raise RuntimeError("Saved content is not a PDF")
                        manifest.append(manifest_row(row, out, url))
                        error = ""
                        break
                    except (PlaywrightTimeoutError, Exception) as exc:
                        error = str(exc)
                        print(f"Browser request failed (attempt {attempt}/{args.retry_count}): {error}", flush=True)
                        if attempt < max(1, args.retry_count):
                            time.sleep(args.retry_delay_seconds * attempt)
                if error:
                    failures.append({**manifest_row(row, out, url), "error": error})
                    if not args.continue_on_error:
                        raise RuntimeError(error)
                if args.request_delay_milliseconds > 0:
                    time.sleep(args.request_delay_milliseconds / 1000)
        finally:
            context.close()

    write_csv(output_dir / "download-index.csv", manifest, fieldnames)
    failures_path = output_dir / "download-failures.csv"
    if failures:
        write_csv(failures_path, failures, failure_fields)
    elif failures_path.exists():
        failures_path.unlink()

    print(f"Browser downloaded {len(manifest)}/{len(rows)} document(s) to {output_dir}", flush=True)
    if failures:
        print(f"Browser failed {len(failures)} document(s). Saved failure index: {failures_path}", flush=True)


if __name__ == "__main__":
    main()
