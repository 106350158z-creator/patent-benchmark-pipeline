from __future__ import annotations

import argparse
import base64
import csv
import re
import time
from pathlib import Path

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


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# Fetch the URL from within the page's own JS context so the request uses the
# real Chrome network stack, TLS fingerprint and cookies (incl. cf_clearance).
# Returns a base64 string on success or a "__ERR__..." / "__STATUS__<code>" marker.
_FETCH_JS = """
async (url) => {
  try {
    const resp = await fetch(url, { credentials: 'include', redirect: 'follow' });
    if (!resp.ok) {
      return '__STATUS__' + resp.status;
    }
    const buf = await resp.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  } catch (e) {
    return '__ERR__' + (e && e.message ? e.message : String(e));
  }
}
"""


def fetch_pdf_via_page(page, url: str, timeout_ms: int, manual_wait_seconds: int) -> bytes:
    deadline = time.time() + max(0, manual_wait_seconds)
    last = ""
    while True:
        result = page.evaluate(_FETCH_JS, url)
        if isinstance(result, str) and not result.startswith("__"):
            data = base64.b64decode(result)
            if data[:4] == b"%PDF":
                return data
            last = f"not-pdf ({len(data)} bytes)"
        else:
            last = str(result)
        if time.time() >= deadline:
            raise RuntimeError(f"page fetch failed: {last}")
        time.sleep(3)


def ensure_origin(page, timeout_ms: int) -> None:
    # Make sure the active page is on register.epo.org so in-page fetch is same-origin.
    try:
        current = page.url or ""
    except Exception:
        current = ""
    if "register.epo.org" not in current:
        page.goto(
            "https://register.epo.org/",
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download selected EPO Register PDFs by attaching over CDP to a real Chrome that has already passed the Cloudflare challenge."
    )
    parser.add_argument("--doclist-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title-regex", default="Communication|Annex|Decision|search report|search opinion|Summons|Minutes|intention to grant")
    parser.add_argument("--download-all", action="store_true")
    parser.add_argument("--earliest-per-title", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
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

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            ensure_origin(page, timeout_ms)

            for row in rows:
                out = output_dir / output_name(row)
                url = document_url(row)
                if valid_pdf(out):
                    print(f"Using cached PDF {row.get('date')} - {row.get('title')}", flush=True)
                    manifest.append(manifest_row(row, out, url))
                    continue
                print(f"CDP downloading {row.get('date')} - {row.get('title')}", flush=True)
                error = ""
                for attempt in range(1, max(1, args.retry_count) + 1):
                    try:
                        body = fetch_pdf_via_page(page, url, timeout_ms, args.manual_wait_seconds)
                        out.write_bytes(body)
                        if not valid_pdf(out):
                            raise RuntimeError("Saved content is not a PDF")
                        manifest.append(manifest_row(row, out, url))
                        error = ""
                        break
                    except Exception as exc:
                        error = str(exc)
                        print(f"CDP request failed (attempt {attempt}/{args.retry_count}): {error}", flush=True)
                        if attempt < max(1, args.retry_count):
                            time.sleep(args.retry_delay_seconds * attempt)
                if error:
                    failures.append({**manifest_row(row, out, url), "error": error})
                    if not args.continue_on_error:
                        raise RuntimeError(error)
                if args.request_delay_milliseconds > 0:
                    time.sleep(args.request_delay_milliseconds / 1000)
        finally:
            # Detach without terminating the user's real Chrome. Closing the
            # CDP-attached browser object would quit Chrome, so we leave it.
            pass

    write_csv(output_dir / "download-index.csv", manifest, fieldnames)
    failures_path = output_dir / "download-failures.csv"
    if failures:
        write_csv(failures_path, failures, failure_fields)
    elif failures_path.exists():
        failures_path.unlink()

    print(f"CDP downloaded {len(manifest)}/{len(rows)} document(s) to {output_dir}", flush=True)
    if failures:
        print(f"CDP failed {len(failures)} document(s). Saved failure index: {failures_path}", flush=True)


if __name__ == "__main__":
    main()
