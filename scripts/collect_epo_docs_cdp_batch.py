from __future__ import annotations

import argparse
import base64
import csv
import json
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


DOCS_TITLE_REGEX = (
    r"^(?!.*translation)("
    r"European search opinion|Supplementary European search report|"
    r"Copy of the international search report|Written opinion of the ISA|"
    r"Copy of the international preliminary report on patentability|"
    r"Communication from the Examining Division|Annex to the communication$|"
    r"Reply to communication from the Examining Division|Amended claims|Claims|"
    r"Description|Published international application|Text intended for grant|"
    r"Communication about intention to grant|Decision to grant|.*refus.*|.*withdrawn.*)"
)
ORIGINAL_TITLE_REGEX = (
    r"^(Application documents|Request for grant of a European patent|Description|"
    r"Claims|Drawings|Abstract|Published international application|"
    r"Bibliographic data of the European patent application)$"
)

MANIFEST_FIELDS = ["applicationNumber", "documentId", "date", "title", "phase", "pages", "fileName", "path", "url"]
STATUS_FIELDS = [
    "index",
    "application_number",
    "publication_number",
    "status",
    "docs_pdf",
    "original_pdf",
    "started_at_utc",
    "finished_at_utc",
    "error",
]

_FETCH_JS = """
async ({url, timeoutMs}) => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { credentials: 'include', redirect: 'follow', signal: ctrl.signal });
    if (!resp.ok) { return '__STATUS__' + resp.status; }
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
  } finally {
    clearTimeout(timer);
  }
}
"""


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
    return f"{date}_{safe_name(row.get('title') or '')}_{row.get('documentId') or ''}.pdf"


def document_url(row: dict[str, str], app: str) -> str:
    return (
        "https://register.epo.org/application?"
        f"documentId={row.get('documentId') or ''}&appnumber={app}&showPdfPage=all&proc="
    )


def select_rows(rows: list[dict[str, str]], title_regex: str, earliest_per_title: bool) -> list[dict[str, str]]:
    pattern = re.compile(title_regex, re.IGNORECASE)
    selected = [row for row in rows if pattern.search(row.get("title") or "")]
    if earliest_per_title:
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in selected:
            grouped.setdefault(title_key(row.get("title") or ""), []).append(row)
        selected = [
            sorted(group, key=lambda r: (date_key(r.get("date") or ""), r.get("documentId") or ""))[0]
            for group in grouped.values()
        ]
    return sorted(selected, key=lambda r: (date_key(r.get("date") or ""), r.get("title") or ""))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def manifest_row(row: dict[str, str], app: str, out: Path, url: str) -> dict[str, str]:
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


class CdpTimeout(Exception):
    """Raised when a CDP op exceeds its hard wall-clock cap (transport wedge)."""


class BlockedError(Exception):
    """Raised when Cloudflare/EPO blocks the node/session (HTTP 403 or aborted
    request). The caller should abort the current case, rotate the node, refresh
    the session, and queue the case for retry instead of burning the manual-wait
    window on every remaining PDF."""


class RateLimitedError(Exception):
    """Raised on HTTP 429 (Too Many Requests): a transient rate limit, not a real
    block. The correct response is an in-place backoff sleep and retry of the same
    URL on the same node -- not a session refresh or node rotation."""


class CdpSession:
    """Owns a dedicated daemon thread holding all Playwright/CDP objects, which
    are thread-affine in the sync API. The main thread submits ops and waits with
    a hard timeout via queue.get; if an op hangs (CDP transport stall after a
    large transfer, or a wedged Cloudflare connection) the worker is abandoned and
    the session is marked dead so the caller can rebuild it while Chrome keeps
    running."""

    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url
        self._cmd_q: "queue.Queue" = queue.Queue()
        self._ready_q: "queue.Queue" = queue.Queue()
        self._alive = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        ok, err = self._ready_q.get()
        if not ok:
            self._alive = False
            raise RuntimeError(f"CDP connect failed: {err}")

    def _run(self) -> None:
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()
                self._ready_q.put((True, ""))
                while True:
                    task = self._cmd_q.get()
                    if task is None:
                        break
                    op, arg, result_q = task
                    try:
                        if op == "evaluate":
                            js, payload, timeout_ms = arg
                            page.set_default_timeout(timeout_ms)
                            value = page.evaluate(js, payload)
                        elif op == "goto":
                            url, timeout_ms = arg
                            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                            value = None
                        elif op == "url":
                            value = page.url or ""
                        else:
                            raise RuntimeError(f"unknown op {op}")
                        result_q.put((True, value))
                    except Exception as exc:  # noqa: BLE001
                        result_q.put((False, exc))
        except Exception as exc:  # noqa: BLE001
            self._ready_q.put((False, exc))

    def _call(self, op: str, arg, hard_timeout_s: float):
        if not self._alive:
            raise CdpTimeout("session dead")
        result_q: "queue.Queue" = queue.Queue(maxsize=1)
        self._cmd_q.put((op, arg, result_q))
        try:
            ok, value = result_q.get(timeout=hard_timeout_s)
        except queue.Empty:
            self._alive = False
            raise CdpTimeout(f"{op} exceeded {hard_timeout_s:.0f}s")
        if not ok:
            msg = str(value).lower()
            if any(s in msg for s in ("has been closed", "target closed", "target page", "connection closed", "browser has been closed", "websocket")):
                self._alive = False
            raise value
        return value

    @property
    def alive(self) -> bool:
        return self._alive

    def evaluate(self, js: str, payload: dict, timeout_ms: int, hard_timeout_s: float):
        return self._call("evaluate", (js, payload, timeout_ms), hard_timeout_s)

    def goto(self, url: str, timeout_ms: int, hard_timeout_s: float) -> None:
        self._call("goto", (url, timeout_ms), hard_timeout_s)

    def url(self, hard_timeout_s: float = 10.0) -> str:
        return self._call("url", None, hard_timeout_s)

    def close(self) -> None:
        try:
            self._cmd_q.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass


def fetch_pdf(session: "CdpSession", url: str, timeout_wait_seconds: int, rl_max_retries: int = 4, rl_base_backoff_s: float = 8.0) -> tuple[bytes, float]:
    """Return (pdf_bytes, elapsed_seconds). Raises on failure.

    HTTP 429 (rate limit) is handled in place with exponential backoff on the
    SAME url/node; only after exhausting rl_max_retries does it raise
    RateLimitedError. A real block (403/503 or an aborted request) raises
    BlockedError immediately instead of burning the whole wait window. Transient
    content issues (non-PDF body, evaluate errors) retry until the deadline."""
    start = time.time()
    deadline = start + max(0, timeout_wait_seconds)
    # Hard per-request cap so a hung in-page fetch can never freeze the batch.
    per_fetch_ms = 30000
    hard_timeout_s = per_fetch_ms / 1000 + 15
    last = ""
    rl_attempts = 0
    while True:
        try:
            result = session.evaluate(_FETCH_JS, {"url": url, "timeoutMs": per_fetch_ms}, per_fetch_ms + 5000, hard_timeout_s)
        except CdpTimeout:
            # Transport is wedged; the session thread can never recover. Propagate
            # so the caller rebuilds the CDP session (Chrome itself stays alive).
            raise
        except Exception as exc:  # evaluate errored / target closed
            msg = str(exc).lower()
            if "aborted" in msg:
                raise BlockedError(f"aborted: {exc}")
            last = f"evaluate:{exc}"
            result = "__ERR__evaluate-error"
        if isinstance(result, str) and not result.startswith("__"):
            data = base64.b64decode(result)
            if data[:4] == b"%PDF":
                return data, time.time() - start
            last = f"not-pdf({len(data)}b)"
        elif isinstance(result, str) and result.startswith("__STATUS__"):
            code = result[len("__STATUS__"):]
            if code == "429":
                # Transient rate limit: back off in place and retry the SAME node.
                if rl_attempts >= rl_max_retries:
                    raise RateLimitedError(f"HTTP 429 after {rl_attempts} backoffs")
                backoff = min(60.0, rl_base_backoff_s * (2 ** rl_attempts))
                rl_attempts += 1
                time.sleep(backoff)
                continue
            if code in ("403", "503"):
                # Definite block on this node/session: stop wasting the wait window.
                raise BlockedError(f"HTTP {code}")
            last = result
        elif isinstance(result, str) and result.startswith("__ERR__"):
            emsg = result[len("__ERR__"):].lower()
            if "aborted" in emsg:
                raise BlockedError(f"aborted: {result}")
            last = result
        else:
            last = str(result)
        if time.time() >= deadline:
            raise RuntimeError(f"fetch failed: {last}")
        time.sleep(3)


def refresh_session(session: "CdpSession", timeout_ms: int = 40000) -> None:
    """Re-navigate to the Register home so a new outbound connection (and, if the
    node was rotated, a new exit IP) establishes a fresh Cloudflare clearance."""
    try:
        session.goto("https://register.epo.org/", timeout_ms, timeout_ms / 1000 + 15)
        time.sleep(1)
    except Exception as exc:  # noqa: BLE001
        print(f"[refresh] navigation warning: {exc}", flush=True)


def rotate_node(project_root: Path, rotate_opts: dict, reason: str) -> bool:
    """Invoke rotate_clash_proxy.py to switch to the next leaf proxy. Returns True on success."""
    if not rotate_opts.get("config") and not rotate_opts.get("controller"):
        return False
    cmd = [
        sys.executable,
        str(project_root / "scripts" / "rotate_clash_proxy.py"),
        "--selector", rotate_opts.get("selector", "node-selection"),
        "--history-file", rotate_opts.get("history_file", "markush-run/_state/clash-node-rotation-cdp.json"),
        "--reason", reason,
        "--bad-cooldown-seconds", str(rotate_opts.get("bad_cooldown_seconds", 1800)),
    ]
    if rotate_opts.get("config"):
        cmd += ["--config", rotate_opts["config"]]
    if rotate_opts.get("controller"):
        cmd += ["--controller", rotate_opts["controller"]]
    try:
        proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, timeout=30)
        print(f"[rotate] {proc.stdout.strip() or proc.stderr.strip()}", flush=True)
        return proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        print(f"[rotate] failed: {exc}", flush=True)
        return False


def download_group(
    session,
    doclist_rows: list[dict[str, str]],
    app: str,
    output_dir: Path,
    title_regex: str,
    earliest_per_title: bool,
    manual_wait_seconds: int,
    request_delay_ms: int,
    slow_threshold_seconds: float = 15.0,
    block_abort_threshold: int = 2,
    rl_max_retries: int = 4,
    rl_base_backoff_s: float = 8.0,
) -> tuple[int, int, list[str], int]:
    rows = select_rows(doclist_rows, title_regex, earliest_per_title)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    errors: list[str] = []
    slow_count = 0
    consecutive_blocks = 0
    for row in rows:
        out = output_dir / output_name(row)
        url = document_url(row, app)
        if valid_pdf(out):
            manifest.append(manifest_row(row, app, out, url))
            continue
        try:
            body, elapsed = fetch_pdf(session, url, manual_wait_seconds, rl_max_retries, rl_base_backoff_s)
            out.write_bytes(body)
            if not valid_pdf(out):
                raise RuntimeError("saved content not PDF")
            manifest.append(manifest_row(row, app, out, url))
            consecutive_blocks = 0
            if elapsed > slow_threshold_seconds:
                slow_count += 1
        except CdpTimeout:
            # Wedged transport: flush what we have, then bubble up so the main
            # loop rebuilds the CDP session before continuing.
            write_csv(output_dir / "download-index.csv", manifest, MANIFEST_FIELDS)
            raise
        except RateLimitedError as exc:
            # 429 survived all in-place backoffs: this node is genuinely
            # overloaded. Treat it like a block so the case aborts and the main
            # loop's tiered recovery (light refresh / rotate) takes over.
            failures.append({**manifest_row(row, app, out, url), "error": f"rate-limited: {exc}"})
            errors.append(f"{row.get('documentId')}:rate-limited:{exc}")
            slow_count += 1
            consecutive_blocks += 1
            if consecutive_blocks >= block_abort_threshold:
                write_csv(output_dir / "download-index.csv", manifest, MANIFEST_FIELDS)
                failures_path = output_dir / "download-failures.csv"
                if failures:
                    write_csv(failures_path, failures, MANIFEST_FIELDS + ["error"])
                raise BlockedError(f"persistent 429: {exc}")
            continue
        except BlockedError as exc:
            # Definite block on this node/session. Fail fast after a couple in a
            # row instead of burning the wait window on every remaining PDF.
            failures.append({**manifest_row(row, app, out, url), "error": f"blocked: {exc}"})
            errors.append(f"{row.get('documentId')}:blocked:{exc}")
            slow_count += 1
            consecutive_blocks += 1
            if consecutive_blocks >= block_abort_threshold:
                write_csv(output_dir / "download-index.csv", manifest, MANIFEST_FIELDS)
                failures_path = output_dir / "download-failures.csv"
                if failures:
                    write_csv(failures_path, failures, MANIFEST_FIELDS + ["error"])
                raise
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append({**manifest_row(row, app, out, url), "error": str(exc)})
            errors.append(f"{row.get('documentId')}:{exc}")
            slow_count += 1
        if request_delay_ms > 0:
            time.sleep(request_delay_ms / 1000)

    write_csv(output_dir / "download-index.csv", manifest, MANIFEST_FIELDS)
    failures_path = output_dir / "download-failures.csv"
    if failures:
        write_csv(failures_path, failures, MANIFEST_FIELDS + ["error"])
    elif failures_path.exists():
        failures_path.unlink()
    return len(manifest), len(rows), errors, slow_count


def load_records(manifest_path: Path, limit: int, offset: int) -> list[dict]:
    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    recs = [r for r in data.get("records", []) if r.get("application_number")]
    return recs[offset : offset + limit if limit else None]


def read_doclist(cache_root: Path, app: str) -> list[dict[str, str]]:
    path = cache_root / app / f"{app}-doclist.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-download EPO Register PDFs by attaching over CDP to a real Chrome that has passed the Cloudflare challenge."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--doclist-cache-root", required=True)
    parser.add_argument("--status-file", default="")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--success-target", type=int, default=0)
    parser.add_argument("--manual-wait-seconds", type=int, default=60)
    parser.add_argument("--request-delay-milliseconds", type=int, default=800)
    parser.add_argument("--skip-existing", action="store_true", help="Skip records whose docs+original already have download-index.csv.")
    parser.add_argument("--slow-rotate-threshold", type=int, default=6, help="If a case has this many slow(>15s)/failed fetches, rotate the Clash node and refresh the session. 0 disables.")
    parser.add_argument("--block-abort-threshold", type=int, default=2, help="Abort a case after this many consecutive 403/aborted fetches, rotate the node, and queue it for retry.")
    parser.add_argument("--max-retries", type=int, default=2, help="How many times a blocked case is re-queued after node rotation before giving up.")
    parser.add_argument("--rotate-after-blocked-cases", type=int, default=3, help="Rotate the Clash node only after this many consecutive blocked cases; below it, just do a lightweight session refresh.")
    parser.add_argument("--rl-max-retries", type=int, default=4, help="On HTTP 429, how many in-place exponential backoffs to do (same node/url) before giving up on the node.")
    parser.add_argument("--rl-base-backoff-seconds", type=float, default=8.0, help="Base backoff seconds for HTTP 429; doubles each attempt, capped at 60s.")
    parser.add_argument("--clash-config", default="", help="Path to Clash/Mihomo config.yaml (for controller/secret/pipe discovery).")
    parser.add_argument("--clash-controller", default="", help="Clash external controller, e.g. 127.0.0.1:9097.")
    parser.add_argument("--clash-selector", default="node-selection", help="Clash selector group to rotate.")
    parser.add_argument("--clash-history-file", default="markush-run/_state/clash-node-rotation-cdp.json")
    parser.add_argument("--clash-bad-cooldown-seconds", type=int, default=1800)
    args = parser.parse_args()

    rotate_opts = {
        "config": args.clash_config,
        "controller": args.clash_controller,
        "selector": args.clash_selector,
        "history_file": args.clash_history_file,
        "bad_cooldown_seconds": args.clash_bad_cooldown_seconds,
    }

    project_root = next(p for p in Path(__file__).resolve().parents if (p / "README.md").exists() and (p / "scripts").exists())
    output_root = (project_root / args.output_root) if not Path(args.output_root).is_absolute() else Path(args.output_root)
    cache_root = (project_root / args.doclist_cache_root) if not Path(args.doclist_cache_root).is_absolute() else Path(args.doclist_cache_root)
    manifest_path = (project_root / args.manifest) if not Path(args.manifest).is_absolute() else Path(args.manifest)
    status_path = Path(args.status_file) if args.status_file else output_root / "batch-collect-cdp-status.csv"
    if not status_path.is_absolute():
        status_path = project_root / status_path

    records = load_records(manifest_path, args.limit, args.offset)
    rows_status: list[dict[str, str]] = []
    status_by_index: dict[int, dict[str, str]] = {}
    counted_ok: set[int] = set()
    successes = 0

    work_queue = deque(
        (index, record, 0)
        for index, record in enumerate(records, start=args.offset + 1)
    )

    session = CdpSession(args.cdp_url)

    def rebuild_session():
        nonlocal session
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
        print("[rebuild] reconnecting CDP session (Chrome stays alive)", flush=True)
        session = CdpSession(args.cdp_url)
        refresh_session(session)

    try:
        try:
            current = session.url()
        except Exception:
            current = ""
        if "register.epo.org" not in current:
            session.goto("https://register.epo.org/", 60000, 75)

        consec_blocked_cases = 0
        while work_queue:
            index, record, attempt = work_queue.popleft()
            if args.success_target and successes >= args.success_target:
                print(f"[stop] reached success target {args.success_target}", flush=True)
                break
            app = str(record.get("application_number") or "").split(".")[0]
            pub = str(record.get("publication_number") or "")
            case_dir = output_root / app
            docs_dir = case_dir / "docs"
            orig_dir = case_dir / "original-application"

            if args.skip_existing and (docs_dir / "download-index.csv").exists() and (orig_dir / "download-index.csv").exists():
                docs_n = sum(1 for _ in docs_dir.glob("*.pdf"))
                orig_n = sum(1 for _ in orig_dir.glob("*.pdf"))
                if docs_n > 0 and orig_n > 0:
                    print(f"[skip] {app} docs={docs_n} original={orig_n}", flush=True)
                    successes += 1
                    continue

            started = datetime.now(timezone.utc).isoformat()
            doclist = read_doclist(cache_root, app)
            status = "ok"
            error = ""
            docs_n = orig_n = 0
            if not doclist:
                status = "error"
                error = "no cached doclist"
            else:
                try:
                    docs_n, docs_total, docs_err, docs_slow = download_group(
                        session, doclist, app, docs_dir, DOCS_TITLE_REGEX, False,
                        args.manual_wait_seconds, args.request_delay_milliseconds,
                        block_abort_threshold=args.block_abort_threshold,
                        rl_max_retries=args.rl_max_retries,
                        rl_base_backoff_s=args.rl_base_backoff_seconds,
                    )
                    orig_n, orig_total, orig_err, orig_slow = download_group(
                        session, doclist, app, orig_dir, ORIGINAL_TITLE_REGEX, True,
                        args.manual_wait_seconds, args.request_delay_milliseconds,
                        block_abort_threshold=args.block_abort_threshold,
                        rl_max_retries=args.rl_max_retries,
                        rl_base_backoff_s=args.rl_base_backoff_seconds,
                    )
                    if docs_n == 0 or orig_n == 0:
                        status = "error"
                        error = f"docs={docs_n}/{docs_total} original={orig_n}/{orig_total}; " + "; ".join((docs_err + orig_err)[:3])

                    # Degradation detection: if this case saw many slow/failed
                    # fetches, Cloudflare has tightened on this node/session.
                    # Rotate the Clash node and refresh the browser session.
                    case_slow = docs_slow + orig_slow
                    if args.slow_rotate_threshold > 0 and case_slow >= args.slow_rotate_threshold:
                        print(f"[degraded] {app} slow/failed fetches={case_slow} >= {args.slow_rotate_threshold}; rotating node + refreshing session", flush=True)
                        rotated = rotate_node(project_root, rotate_opts, f"cdp_slow:{app}:{case_slow}")
                        refresh_session(session)
                        if rotated:
                            print(f"[degraded] rotation done; continuing", flush=True)
                except BlockedError as exc:
                    # Tiered recovery: a single block just does a lightweight
                    # session refresh (no IP change). Only when several cases in a
                    # row are blocked do we pay for a full node rotation, since
                    # that is the expensive part and only worth it if the node is
                    # genuinely burned.
                    consec_blocked_cases += 1
                    if consec_blocked_cases >= args.rotate_after_blocked_cases:
                        print(f"[blocked] {app}: {exc}; {consec_blocked_cases} cases blocked in a row -> rotating node (attempt {attempt + 1})", flush=True)
                        rotate_node(project_root, rotate_opts, f"cdp_blocked_streak:{app}:{consec_blocked_cases}")
                        consec_blocked_cases = 0
                        if not session.alive:
                            rebuild_session()
                        else:
                            refresh_session(session)
                    else:
                        print(f"[blocked] {app}: {exc}; light refresh (attempt {attempt + 1})", flush=True)
                        if not session.alive:
                            rebuild_session()
                        else:
                            refresh_session(session)
                    if attempt < args.max_retries:
                        work_queue.append((index, record, attempt + 1))
                        print(f"[blocked-retry] {app} re-queued (attempt {attempt + 1}/{args.max_retries})", flush=True)
                        continue
                    status = "error"
                    error = f"blocked (gave up after {args.max_retries} retries): {exc}"
                except CdpTimeout as exc:
                    # Transport wedged mid-case: rotate, rebuild the session, move on.
                    status = "error"
                    error = f"cdp-timeout: {exc}"
                    print(f"[wedged] {app}: {exc}; rotating node + rebuilding session", flush=True)
                    if args.slow_rotate_threshold > 0:
                        rotate_node(project_root, rotate_opts, f"cdp_timeout:{app}")
                    rebuild_session()
                except Exception as exc:  # noqa: BLE001
                    status = "error"
                    error = repr(exc)
                    # A hard error may also mean the session died; try to recover.
                    if args.slow_rotate_threshold > 0:
                        rotate_node(project_root, rotate_opts, f"cdp_error:{app}")
                    if not session.alive:
                        rebuild_session()
                    else:
                        refresh_session(session)

            if status == "ok" and index not in counted_ok:
                counted_ok.add(index)
                successes += 1
            if status == "ok":
                consec_blocked_cases = 0
            status_by_index[index] = {
                "index": str(index),
                "application_number": app,
                "publication_number": pub,
                "status": status,
                "docs_pdf": str(docs_n),
                "original_pdf": str(orig_n),
                "started_at_utc": started,
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "error": error,
            }
            rows_status = [status_by_index[k] for k in sorted(status_by_index)]
            write_csv(status_path, rows_status, STATUS_FIELDS)
            print(f"[done] {app}: {status} docs={docs_n} original={orig_n} successes={successes}", flush=True)
    finally:
        # Detach without terminating the user's real Chrome: close() only stops
        # the worker thread and the Playwright connection, never quits Chrome.
        session.close()

    write_csv(status_path, rows_status, STATUS_FIELDS)
    ok = sum(1 for r in rows_status if r["status"] == "ok")
    err = sum(1 for r in rows_status if r["status"] == "error")
    print(f"Wrote status: {status_path}")
    print(f"ok={ok} error={err}")


if __name__ == "__main__":
    main()
