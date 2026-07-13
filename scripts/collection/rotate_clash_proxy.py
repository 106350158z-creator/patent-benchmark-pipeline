from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GROUP_TYPES = {"selector", "urltest", "url-test", "fallback", "loadbalance", "load-balance", "relay"}
DEFAULT_EXCLUDE_PATTERN = (
    r"(?i)剩余流量|流量|套餐|到期|重置|官网|订阅|更新|expire|traffic|"
    r"subscription|renew|reset|剩余|距离下次"
)


def normalize_controller(value: str) -> str:
    text = (value or "").strip() or "127.0.0.1:9090"
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    return text.rstrip("/")


def read_clash_config(path: Path) -> tuple[str, str]:
    controller = ""
    secret = ""
    pipe = ""
    if not path.exists():
        return controller, secret
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip("'\"")
        if key.strip() == "external-controller":
            controller = value
        elif key.strip() == "external-controller-pipe":
            pipe = value
        elif key.strip() == "secret":
            secret = value
    return controller, secret, pipe


def decode_chunked(body: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while True:
        line_end = body.find(b"\r\n", pos)
        if line_end < 0:
            return bytes(out) if out else body
        size_text = body[pos:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError:
            return body
        pos = line_end + 2
        if size == 0:
            return bytes(out)
        out.extend(body[pos : pos + size])
        pos += size + 2


def parse_http_response(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    header_bytes, _, body = raw.partition(b"\r\n\r\n")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    if "transfer-encoding: chunked" in header_text.lower():
        body = decode_chunked(body)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def request_json(controller: str, path: str, secret: str = "", method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = controller + path
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read()
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def request_json_pipe(pipe: str, path: str, secret: str = "", method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        import win32file
    except Exception as exc:  # pragma: no cover - Windows-only fallback
        raise RuntimeError("pywin32 is required for Clash named-pipe control") from exc

    body = b""
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: localhost",
        "Accept: application/json",
        "Connection: close",
    ]
    if secret:
        headers.append(f"Authorization: Bearer {secret}")
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers.append("Content-Type: application/json")
        headers.append(f"Content-Length: {len(body)}")
    request = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body
    handle = win32file.CreateFile(
        pipe,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )
    try:
        win32file.WriteFile(handle, request)
        chunks: list[bytes] = []
        while True:
            try:
                _, data = win32file.ReadFile(handle, 65536)
            except Exception:
                break
            if not data:
                break
            chunks.append(data)
    finally:
        win32file.CloseHandle(handle)
    return parse_http_response(b"".join(chunks))


class ClashClient:
    def __init__(self, controller: str, pipe: str, secret: str):
        self.controller = controller
        self.pipe = pipe
        self.secret = secret
        self.transport = "http"

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.controller:
            try:
                self.transport = "http"
                return request_json(self.controller, path, self.secret, method=method, payload=payload)
            except (urllib.error.URLError, TimeoutError, OSError):
                if not self.pipe:
                    raise
        if self.pipe:
            self.transport = "pipe"
            return request_json_pipe(self.pipe, path, self.secret, method=method, payload=payload)
        raise RuntimeError("No Clash controller or named pipe is configured")


def get_proxy(client: ClashClient, name: str) -> dict[str, Any]:
    return client.request("/proxies/" + urllib.parse.quote(name, safe=""))


def select_proxy(client: ClashClient, selector: str, name: str) -> None:
    client.request(
        "/proxies/" + urllib.parse.quote(selector, safe=""),
        method="PUT",
        payload={"name": name},
    )


def resolve_selector_name(requested: str, proxy_map: dict[str, Any]) -> str:
    if requested in proxy_map:
        return requested
    alias_suffixes = {
        "node-selection": "\u8282\u70b9\u9009\u62e9",
        "selector": "\u8282\u70b9\u9009\u62e9",
    }
    suffix = alias_suffixes.get((requested or "").strip().lower(), requested or "")
    if "\u947a\u50ad" in suffix or "\u99c3\u6bb7" in suffix:
        suffix = "\u8282\u70b9\u9009\u62e9"
    for name, info in proxy_map.items():
        if name.endswith(suffix) and isinstance(info, dict) and info.get("all"):
            return name
    for name, info in proxy_map.items():
        if suffix and suffix in name and isinstance(info, dict) and info.get("all"):
            return name
    return requested


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"bad_nodes": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"bad_nodes": {}}


def write_history(path: Path, history: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def recently_bad(history: dict[str, Any], name: str, cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return False
    item = (history.get("bad_nodes") or {}).get(name) or {}
    marked = item.get("marked_at_epoch")
    if not marked:
        return False
    return time.time() - float(marked) < cooldown_seconds


def mark_bad(history: dict[str, Any], name: str, reason: str) -> None:
    if not name:
        return
    history.setdefault("bad_nodes", {})[name] = {
        "marked_at_epoch": time.time(),
        "marked_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }


def is_leaf_proxy(name: str, proxy_map: dict[str, Any]) -> bool:
    if name in {"DIRECT", "REJECT", "GLOBAL"}:
        return False
    item = proxy_map.get(name) or {}
    proxy_type = str(item.get("type") or "").lower()
    if proxy_type in GROUP_TYPES:
        return False
    if item.get("all"):
        return False
    return True


def choose_next(
    selector_all: list[str],
    current: str,
    proxy_map: dict[str, Any],
    history: dict[str, Any],
    include_re: re.Pattern[str] | None,
    exclude_re: re.Pattern[str] | None,
    cooldown_seconds: int,
) -> str:
    if not selector_all:
        return ""
    try:
        start = selector_all.index(current) + 1
    except ValueError:
        start = 0
    ordered = selector_all[start:] + selector_all[:start]
    candidates: list[str] = []
    for name in ordered:
        if name == current:
            continue
        if not is_leaf_proxy(name, proxy_map):
            continue
        if include_re and not include_re.search(name):
            continue
        if exclude_re and exclude_re.search(name):
            continue
        if recently_bad(history, name, cooldown_seconds):
            continue
        candidates.append(name)
    if candidates:
        return candidates[0]
    for name in ordered:
        if name != current and is_leaf_proxy(name, proxy_map):
            return name
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate a Clash/Mihomo selector group to the next leaf proxy.")
    parser.add_argument("--config", default=os.environ.get("CLASH_CONFIG", ""))
    parser.add_argument("--controller", default=os.environ.get("CLASH_CONTROLLER", ""))
    parser.add_argument("--pipe", default=os.environ.get("CLASH_PIPE", ""))
    parser.add_argument("--secret", default=os.environ.get("CLASH_SECRET", ""))
    parser.add_argument("--selector", default=os.environ.get("CLASH_SELECTOR", "node-selection"))
    parser.add_argument("--history-file", default="markush-run/_state/clash-node-rotation.json")
    parser.add_argument("--reason", default="epo_fetch_failure")
    parser.add_argument("--include-regex", default=os.environ.get("CLASH_ROTATE_INCLUDE", ""))
    parser.add_argument("--exclude-regex", default=os.environ.get("CLASH_ROTATE_EXCLUDE", DEFAULT_EXCLUDE_PATTERN))
    parser.add_argument("--bad-cooldown-seconds", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_controller = ""
    config_secret = ""
    config_pipe = ""
    if args.config:
        config_controller, config_secret, config_pipe = read_clash_config(Path(args.config))
    controller = normalize_controller(args.controller or config_controller or "127.0.0.1:9090")
    pipe = args.pipe or config_pipe
    secret = args.secret or config_secret
    client = ClashClient(controller, pipe, secret)
    history_path = Path(args.history_file)
    history = load_history(history_path)
    include_re = re.compile(args.include_regex) if args.include_regex else None
    exclude_re = re.compile(args.exclude_regex) if args.exclude_regex else None

    try:
        proxy_payload = client.request("/proxies")
        proxy_map = proxy_payload.get("proxies") or {}
        selector = resolve_selector_name(args.selector, proxy_map)
        selector_info = proxy_map.get(selector) or get_proxy(client, selector)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pipe_note = f" or pipe {pipe}" if pipe else ""
        raise SystemExit(f"Clash controller unavailable at {controller}{pipe_note}: {exc}") from exc

    current = str(selector_info.get("now") or "")
    selector_all = list(selector_info.get("all") or [])
    next_node = choose_next(
        selector_all,
        current,
        proxy_map,
        history,
        include_re,
        exclude_re,
        args.bad_cooldown_seconds,
    )
    if not next_node:
        raise SystemExit(f"No candidate leaf proxy found in selector {args.selector!r}")

    mark_bad(history, current, args.reason)
    event = {
        "rotated_at_utc": datetime.now(timezone.utc).isoformat(),
        "controller": controller,
        "transport": client.transport,
        "selector": selector,
        "from": current,
        "to": next_node,
        "reason": args.reason,
        "dry_run": args.dry_run,
    }
    history.setdefault("events", []).append(event)
    history["current"] = next_node
    if not args.dry_run:
        select_proxy(client, selector, next_node)
    write_history(history_path, history)
    print(json.dumps(event, ensure_ascii=True))


if __name__ == "__main__":
    main()
