from __future__ import annotations

import argparse
import re
from pathlib import Path


BASE_URL = "https://register.epo.org/"


LOCAL_FALLBACK_CSS = """
<style id="local-epo-fallback">
body { font-family: Arial, Helvetica, sans-serif; margin: 16px; color: #222; background: #fff; }
a { color: #0645ad; }
table { border-collapse: collapse; max-width: 100%; }
td, th { border: 1px solid #c8d0d8; padding: 4px 6px; vertical-align: top; }
.th { background: #eef2f6; font-weight: bold; }
.t1 { background: #f7f7f7; }
.former { color: #555; }
#epoHeader, #epoFooter, .noPrint { margin: 12px 0; }
</style>
""".strip()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_scripts(html: str) -> str:
    # The EPO Register main page is static enough for our extraction and local
    # reading. Removing scripts avoids Cloudflare/challenge code and file://
    # resource failures blocking local rendering.
    return re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", html)


def _strip_external_resource_links(html: str) -> str:
    html = re.sub(
        r"(?is)<link\b(?=[^>]*\brel\s*=\s*['\"]?stylesheet\b)[^>]*>\s*",
        "",
        html,
    )
    html = re.sub(
        r"(?is)<link\b(?=[^>]*\brel\s*=\s*['\"]?(?:shortcut\s+icon|icon)\b)[^>]*>\s*",
        "",
        html,
    )
    return html


def _ensure_base(html: str) -> str:
    if re.search(r"(?is)<base\b", html):
        return html
    return re.sub(r"(?is)<head\b([^>]*)>", rf'<head\1>\n<base href="{BASE_URL}">', html, count=1)


def _rewrite_root_attrs(html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        attr, quote, value = match.group(1), match.group(2), match.group(3)
        if value.startswith("//"):
            return match.group(0)
        return f'{attr}={quote}{BASE_URL.rstrip("/")}{value}{quote}'

    return re.sub(
        r'\b(href|src|action)\s*=\s*(["\'])(/[^"\']*)\2',
        replace,
        html,
        flags=re.IGNORECASE,
    )


def _add_fallback_css(html: str) -> str:
    if "local-epo-fallback" in html:
        return html
    return re.sub(r"(?is)</head>", LOCAL_FALLBACK_CSS + "\n</head>", html, count=1)


def sanitize(html: str) -> str:
    html = _strip_scripts(html)
    html = _strip_external_resource_links(html)
    html = _ensure_base(html)
    html = _rewrite_root_attrs(html)
    html = _add_fallback_css(html)
    return html


def main() -> int:
    parser = argparse.ArgumentParser(description="Make saved EPO Register HTML readable from file://.")
    parser.add_argument("html", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    if args.in_place and args.output:
        parser.error("--in-place and --output are mutually exclusive")

    source = args.html
    target = source if args.in_place else args.output
    if target is None:
        parser.error("pass --in-place or --output")

    cleaned = sanitize(_read_text(source))
    target.write_text(cleaned, encoding="utf-8", newline="\n")
    print(f"Sanitized EPO HTML: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
