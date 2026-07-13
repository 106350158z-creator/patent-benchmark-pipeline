from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any


DOC_ROW_RE = re.compile(
    r'(?s)<tr>\s*<td class="smallBorder">.*?<input[^>]+value="([^"]+)".*?</td>\s*'
    r"<td>([^<]+)</td>\s*"
    r'<td class="nowrap"><a id="[^"]+".*?>(.*?)</a></td>\s*'
    r"<td>(.*?)</td>\s*"
    r'<td class="right noOfPages">([^<]+)</td>'
)
REJECTED_RE = re.compile(
    r"has rejected your request|rejected your request|restrictedrequest|just a moment|"
    r"__cf_chl|cf-chl-widget|challenge-form|cf-mitigated|verify you are human|正在进行安全验证|请验证您是真人",
    re.I,
)
TAG_RE = re.compile(r"<.*?>", re.S)


def normalize_app(value: str) -> str:
    app = value.strip().upper().split(".")[0]
    if app and not app.startswith("EP"):
        app = "EP" + app
    return app


def clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAG_RE.sub(" ", value).replace("&nbsp;", " "))).strip()


def is_rejected_html(html: str) -> bool:
    return bool(REJECTED_RE.search(html or ""))


def parse_doc_rows(html: str, app: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in DOC_ROW_RE.finditer(html or ""):
        pages_text = clean_cell(match.group(5))
        try:
            pages = int(pages_text)
        except ValueError:
            pages = 0
        rows.append(
            {
                "applicationNumber": app,
                "documentId": unescape(match.group(1).strip()),
                "date": clean_cell(match.group(2)),
                "title": clean_cell(match.group(3)),
                "phase": clean_cell(match.group(4)),
                "pages": pages,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["applicationNumber", "documentId", "date", "title", "phase", "pages"])
        writer.writeheader()
        writer.writerows(rows)


def read_cached_ok(html_path: Path, csv_path: Path) -> bool:
    if not html_path.exists() or not csv_path.exists():
        return False
    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")
        if is_rejected_html(html):
            return False
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle)) > 0
    except Exception:
        return False


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["written_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def fetch_in_page(page, url: str) -> dict[str, Any]:
    return await page.evaluate(
        """async (url) => {
            try {
                const resp = await fetch(url, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {'Accept': 'text/html,*/*'}
                });
                const text = await resp.text();
                return {
                    ok: resp.ok,
                    status: resp.status,
                    url: resp.url,
                    contentType: resp.headers.get('content-type') || '',
                    text
                };
            } catch (error) {
                return {ok: false, status: 0, url, contentType: '', text: '', error: String(error)};
            }
        }""",
        url,
    )


async def run(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        print("Missing playwright. Install with: pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 2

    app = normalize_app(args.application_number)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tab = args.tab
    html_path = output_dir / f"{app}-{tab}.html"
    csv_path = output_dir / f"{app}-doclist.csv"
    status_path = Path(args.status_file) if args.status_file else output_dir / f"{app}-{tab}-status.json"
    url = f"https://register.epo.org/application?number={app}&lng=en&tab={tab}"

    if args.use_cached:
        if tab == "doclist" and read_cached_ok(html_path, csv_path):
            write_status(status_path, {"applicationNumber": app, "tab": tab, "status": "cached", "html": str(html_path), "csv": str(csv_path)})
            print(f"Using cached browser doclist: {csv_path}")
            return 0
        if tab == "main" and html_path.exists():
            html = html_path.read_text(encoding="utf-8", errors="replace")
            if html.strip() and not is_rejected_html(html):
                write_status(status_path, {"applicationNumber": app, "tab": tab, "status": "cached", "html": str(html_path)})
                print(f"Using cached browser main: {html_path}")
                return 0

    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir.resolve()),
        "headless": args.headless,
        "viewport": {"width": 1365, "height": 900},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if args.start_minimized:
        launch_kwargs["args"].append("--start-minimized")
    if args.proxy_server:
        launch_kwargs["proxy"] = {"server": args.proxy_server}

    last_html = ""
    last_status: dict[str, Any] = {}
    deadline = time.time() + max(1, args.manual_wait_seconds)

    async with async_playwright() as p:
        channels = [args.browser_channel] if args.browser_channel else []
        for fallback_channel in ("chrome", "msedge", ""):
            if fallback_channel not in channels:
                channels.append(fallback_channel)
        last_launch_error: Exception | None = None
        for channel in channels:
            try:
                if channel:
                    context = await p.chromium.launch_persistent_context(channel=channel, **launch_kwargs)
                else:
                    context = await p.chromium.launch_persistent_context(**launch_kwargs)
                break
            except Exception as exc:
                last_launch_error = exc
        else:
            raise RuntimeError(f"Unable to launch browser fallback: {last_launch_error}") from last_launch_error
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=args.navigation_timeout_seconds * 1000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass

            while True:
                await page.wait_for_timeout(max(0, int(args.poll_seconds * 1000)))
                result = await fetch_in_page(page, url)
                html = str(result.get("text") or "")
                if not html:
                    try:
                        html = await page.evaluate("document.documentElement.outerHTML")
                    except Exception:
                        html = ""
                last_html = html
                rows = parse_doc_rows(html, app) if tab == "doclist" else []
                rejected = is_rejected_html(html)
                last_status = {
                    "applicationNumber": app,
                    "tab": tab,
                    "status": "pending",
                    "url": url,
                    "fetch_status": result.get("status"),
                    "fetch_ok": result.get("ok"),
                    "content_type": result.get("contentType"),
                    "row_count": len(rows),
                    "rejected": rejected,
                    "error": result.get("error", ""),
                    "profile_dir": str(profile_dir),
                    "proxy_server": args.proxy_server,
                    "headless": args.headless,
                }
                if ((tab == "doclist" and rows) or (tab == "main" and len(html) > 1000)) and not rejected:
                    html_path.write_text(html, encoding="utf-8", newline="\n")
                    if tab == "doclist":
                        write_csv(csv_path, rows)
                    if args.sanitize:
                        sanitize_script = Path(__file__).resolve().parent / "sanitize_epo_html.py"
                        if sanitize_script.exists():
                            proc = await asyncio.create_subprocess_exec(
                                sys.executable,
                                str(sanitize_script),
                                str(html_path),
                                "--in-place",
                            )
                            await proc.wait()
                    last_status.update({"status": "ok", "html": str(html_path)})
                    if tab == "doclist":
                        last_status["csv"] = str(csv_path)
                    write_status(status_path, last_status)
                    print(f"Saved HTML: {html_path}")
                    if tab == "doclist":
                        print(f"Saved CSV : {csv_path}")
                        print(f"Rows      : {len(rows)}")
                    return 0
                if time.time() >= deadline:
                    break
                if rejected and not args.headless:
                    print("Waiting for browser challenge/manual verification...", flush=True)
        finally:
            await context.close()

    if last_html:
        html_path.write_text(last_html, encoding="utf-8", newline="\n")
    rows = parse_doc_rows(last_html, app) if tab == "doclist" else []
    if tab == "doclist" and rows:
        write_csv(csv_path, rows)
    status = "cf_challenge" if is_rejected_html(last_html) else "no_rows"
    last_status.update({"status": status, "html": str(html_path), "csv": str(csv_path) if rows else "", "row_count": len(rows), "tab": tab})
    write_status(status_path, last_status)
    print(f"Browser doclist fetch failed: {status}", file=sys.stderr)
    print(f"Saved diagnostic HTML: {html_path}", file=sys.stderr)
    return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch EPO Register doclist through a persistent browser session.")
    parser.add_argument("-ApplicationNumber", "--application-number", required=True)
    parser.add_argument("-OutputDir", "--output-dir", default=".")
    parser.add_argument("--tab", choices=["main", "doclist"], default="doclist")
    parser.add_argument("--profile-dir", default="markush-run/_state/epo-register-browser-profile")
    parser.add_argument("--proxy-server", default="")
    parser.add_argument("--browser-channel", default="chrome")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--start-minimized", action="store_true")
    parser.add_argument("--manual-wait-seconds", type=int, default=180)
    parser.add_argument("--navigation-timeout-seconds", type=int, default=120)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--status-file", default="")
    parser.add_argument("--use-cached", action="store_true")
    parser.add_argument("--no-sanitize", dest="sanitize", action="store_false")
    parser.set_defaults(sanitize=True)
    return parser


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
