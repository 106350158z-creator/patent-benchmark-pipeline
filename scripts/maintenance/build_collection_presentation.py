from __future__ import annotations

import csv
import html
import json
import math
from collections import Counter
from pathlib import Path


ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "README.md").exists() and (parent / "scripts").exists()
)
INDEX_PATH = ROOT / "markush-run" / "benchmark" / "collection-index.json"
AUDIT_PATH = ROOT / "markush-run" / "benchmark" / "patents" / "_raw_file_completeness_audit.csv"
MANIFEST_PATH = (
    ROOT
    / "markush-run"
    / "_backup_before_reorganize_20260704-145842"
    / "benchmark_previous_contents"
    / "ep_application_candidates_overnight_20260703-175215_merged.json"
)
OUTPUT_PATH = ROOT / "文档" / "3000样本抓取情况组会展示.html"


COLORS = [
    "#2563eb",
    "#16a34a",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#06b6d4",
    "#64748b",
    "#ec4899",
    "#84cc16",
    "#f97316",
    "#14b8a6",
    "#7c3aed",
]


def pct(value: int, total: int) -> float:
    return round(value * 100 / total, 1) if total else 0.0


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def items_from_counter(
    counter: Counter[str],
    label_map: dict[str, str] | None = None,
    top: int | None = None,
    other_label: str = "其他",
) -> list[tuple[str, int]]:
    label_map = label_map or {}
    pairs = [(label_map.get(key, key), value) for key, value in counter.items()]
    pairs.sort(key=lambda item: item[1], reverse=True)
    if top and len(pairs) > top:
        head = pairs[:top]
        return head + [(other_label, sum(value for _, value in pairs[top:]))]
    return pairs


def make_pie(title: str, subtitle: str, items: list[tuple[str, int]], donut: bool = True) -> str:
    total = sum(value for _, value in items) or 1
    current = 0.0
    segments: list[str] = []
    cx = cy = 90
    radius = 78

    def point(angle: float) -> tuple[float, float]:
        radians = math.radians(angle - 90)
        return cx + radius * math.cos(radians), cy + radius * math.sin(radians)

    for index, (label, value) in enumerate(items):
        if value <= 0:
            continue
        start = current / total * 360
        current += value
        end = current / total * 360
        color = COLORS[index % len(COLORS)]
        if end - start >= 359.99:
            d = f"M {cx} {cy - radius} A {radius} {radius} 0 1 1 {cx - 0.01} {cy - radius} Z"
        else:
            x1, y1 = point(start)
            x2, y2 = point(end)
            large = 1 if end - start > 180 else 0
            d = f"M {cx} {cy} L {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large} 1 {x2:.2f} {y2:.2f} Z"
        segments.append(
            f'<path d="{d}" fill="{color}"><title>{html.escape(label)} {value} ({pct(value, total)}%)</title></path>'
        )

    hole = '<circle cx="90" cy="90" r="43" fill="#fff"/>' if donut else ""
    center = (
        f'<text x="90" y="85" text-anchor="middle" class="pie-num">{total}</text>'
        '<text x="90" y="106" text-anchor="middle" class="pie-sub">total</text>'
        if donut
        else ""
    )
    legend = "".join(
        f'<li><span style="background:{COLORS[index % len(COLORS)]}"></span>'
        f"<b>{html.escape(label)}</b><em>{value} / {pct(value, total)}%</em></li>"
        for index, (label, value) in enumerate(items)
    )
    return f"""
      <section class="chart-card">
        <div class="chart-copy"><h3>{html.escape(title)}</h3><p>{html.escape(subtitle)}</p></div>
        <div class="pie-row"><svg class="pie" viewBox="0 0 180 180" role="img">{''.join(segments)}{hole}{center}</svg><ul class="legend">{legend}</ul></div>
      </section>"""


def make_bars(title: str, subtitle: str, items: list[tuple[str, int]]) -> str:
    max_value = max([value for _, value in items] or [1])
    rows = ""
    for index, (label, value) in enumerate(items):
        rows += (
            f'<div class="bar-row"><div class="bar-label">{html.escape(label)}</div>'
            f'<div class="bar-track"><span style="width:{value / max_value * 100:.1f}%;background:{COLORS[index % len(COLORS)]}"></span></div>'
            f'<div class="bar-val">{value}</div></div>'
        )
    return f'<section class="wide-card"><h3>{html.escape(title)}</h3><p>{html.escape(subtitle)}</p><div class="bars">{rows}</div></section>'


def main() -> None:
    index = load_json(INDEX_PATH)
    cases = index["cases"]
    summary = index["summary"]
    total = len(cases)
    manifest = load_json(MANIFEST_PATH)
    records = manifest["records"]

    class_counts = Counter(case["primary_class"] for case in cases)
    original_stage = Counter(str(case.get("ledger_original_stage") or "未记录") for case in cases)
    docs_stage = Counter(str(case.get("ledger_docs_stage") or "未记录") for case in cases)

    label_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    keyword_counts: Counter[str] = Counter()
    source_query_counts: Counter[str] = Counter()
    for record in records:
        label = str(record.get("benchmark_label") or "")
        if label.startswith("positive_granted"):
            label_counts["授权/授权公开样本"] += 1
        elif label.startswith("negative"):
            label_counts["未授权/负样本候选"] += 1
        else:
            label_counts["未标注"] += 1
        category_counts[str(record.get("category") or record.get("keyword_category") or "未分类")] += 1
        keyword_counts[str(record.get("keyword_group") or "未记录")] += 1
        source_query_counts[str(record.get("source_query") or record.get("matched_query") or "未记录")] += 1

    issue_counts: Counter[str] = Counter()
    min_complete = 0
    with AUDIT_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("min_complete") == "True":
                min_complete += 1
            for issue in filter(None, (row.get("issues") or "").split(";")):
                issue_counts[issue] += 1

    class_label = {
        "A_strict_complete": "A 严格完整旧口径",
        "C_original_pdf_text_ready": "C 原始PDF+文本",
        "D_original_pdf_needs_text_extract": "D 原始PDF待抽文",
        "E_original_fallback_only": "E 仅HTML/XML fallback",
        "F_file_wrapper_docs_only": "F 仅审查docs",
        "G_register_metadata_only": "G 仅Register",
        "H_misc_or_failed_artifacts": "H 其他/失败残留",
        "I_empty_case_dir": "I 空目录",
    }
    stage_label = {
        "success_pdf": "成功：PDF",
        "success_pdf_text": "成功：PDF+文本",
        "success_fallback": "仅fallback",
        "not_started": "未开始",
        "publication_unavailable": "公开不可用",
        "failed": "失败",
        "register_seen_no_docs": "有Register但无docs",
    }
    issue_label = {
        "missing_docs_download_index": "缺docs下载索引",
        "missing_valid_docs_pdf": "缺有效审查PDF",
        "missing_register_doclist_csv": "缺doclist CSV",
        "missing_register_doclist_html": "缺doclist HTML",
        "missing_register_main": "缺Register main",
        "missing_valid_original_pdf": "缺有效原始PDF",
        "missing_original_download_index": "缺原始PDF索引",
    }
    category_label = {
        "oncology_target": "肿瘤靶点",
        "antibacterial_tb": "抗结核/抗菌",
        "antifungal": "抗真菌",
        "antibacterial": "抗菌",
        "未分类": "未分类",
    }

    charts = [
        make_pie("授权 / 未授权样本", "来自候选manifest的benchmark_label。", items_from_counter(label_counts), True),
        make_pie("当前采集分桶", "A/C/D 被旧口径归为complete，但只有A接近完整审查链路。", items_from_counter(class_counts, class_label), True),
        make_pie("原始公开材料抓取结果", "Publication Server链路：PDF成功、PDF+文本、fallback、未开始或不可用。", items_from_counter(original_stage, stage_label), True),
        make_pie("审查案卷docs阶段", "Register file wrapper链路：多数样本尚未进入docs抓取。", items_from_counter(docs_stage, stage_label), True),
        make_pie("领域大类分布", "来自候选manifest的category字段。", items_from_counter(category_counts, category_label), True),
        make_pie("严格闭环完整性", "按报告所需原始文件：Register三件套 + docs有效PDF + original有效PDF。", [("完整闭环", min_complete), ("不完整/待补抓", total - min_complete)], True),
    ]

    keyword_items = items_from_counter(keyword_counts, top=14, other_label="其他关键词")
    query_items = items_from_counter(source_query_counts, top=12, other_label="其他查询词")
    issue_items = items_from_counter(issue_counts, issue_label, top=8, other_label="其他缺口")

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>EPO 3000+样本抓取情况组会展示</title>
<style>
  :root {{ --ink:#111827; --muted:#64748b; --line:#e5e7eb; --bg:#f8fafc; --panel:#ffffff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family: Arial, 'Microsoft YaHei', sans-serif; color:var(--ink); background:var(--bg); line-height:1.55; }}
  .hero {{ min-height:88vh; display:grid; align-items:end; padding:54px 6vw 38px; background:linear-gradient(120deg, rgba(37,99,235,.92), rgba(20,184,166,.78)), url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 720"><rect width="1200" height="720" fill="%23e5edf8"/><g fill="none" stroke="%23ffffff" stroke-opacity=".32"><path d="M80 130h980M120 230h900M90 340h990M160 470h840M110 590h970"/><path d="M180 90v540M360 60v590M560 100v520M760 80v570M960 120v500"/></g><g fill="%23ffffff" fill-opacity=".28"><circle cx="260" cy="210" r="54"/><circle cx="820" cy="370" r="80"/><circle cx="1040" cy="170" r="42"/></g></svg>') center/cover; color:#fff; }}
  .hero-inner {{ max-width:1120px; }}
  .eyebrow {{ text-transform:uppercase; letter-spacing:.08em; font-size:14px; opacity:.86; font-weight:700; }}
  h1 {{ font-size: clamp(40px, 7vw, 86px); line-height:1.03; margin:18px 0; letter-spacing:0; }}
  .hero p {{ max-width:850px; font-size:20px; opacity:.94; }}
  .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-top:34px; }}
  .metric {{ border:1px solid rgba(255,255,255,.4); padding:18px; background:rgba(255,255,255,.14); backdrop-filter: blur(6px); }}
  .metric strong {{ display:block; font-size:34px; line-height:1; }}
  .metric span {{ display:block; margin-top:8px; opacity:.9; }}
  main {{ padding:42px 5vw 80px; }}
  .section-title {{ max-width:1100px; margin:38px auto 18px; }}
  .section-title h2 {{ font-size:30px; margin:0 0 8px; }}
  .section-title p {{ margin:0; color:var(--muted); }}
  .grid {{ max-width:1180px; margin:0 auto; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }}
  .chart-card,.wide-card,.note-card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; box-shadow:0 8px 24px rgba(15,23,42,.05); }}
  .chart-copy h3,.wide-card h3,.note-card h3 {{ margin:0 0 6px; font-size:20px; }}
  .chart-copy p,.wide-card p,.note-card p {{ margin:0 0 16px; color:var(--muted); }}
  .pie-row {{ display:grid; grid-template-columns:200px 1fr; gap:16px; align-items:center; }}
  .pie {{ width:180px; height:180px; }}
  .pie-num {{ font-size:24px; font-weight:800; fill:#111827; }}
  .pie-sub {{ font-size:12px; fill:#64748b; }}
  .legend {{ list-style:none; padding:0; margin:0; display:grid; gap:7px; }}
  .legend li {{ display:grid; grid-template-columns:12px 1fr auto; gap:8px; align-items:center; font-size:13px; }}
  .legend span {{ width:12px; height:12px; border-radius:2px; }}
  .legend em {{ color:var(--muted); font-style:normal; }}
  .wide {{ max-width:1180px; margin:18px auto 0; }}
  .bars {{ display:grid; gap:10px; }}
  .bar-row {{ display:grid; grid-template-columns:190px 1fr 64px; gap:10px; align-items:center; font-size:14px; }}
  .bar-label {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .bar-track {{ height:14px; background:#eef2f7; border-radius:999px; overflow:hidden; }}
  .bar-track span {{ display:block; height:100%; border-radius:999px; }}
  .bar-val {{ text-align:right; color:#475569; font-variant-numeric:tabular-nums; }}
  .story {{ max-width:1180px; margin:0 auto; display:grid; grid-template-columns:1.1fr .9fr; gap:18px; }}
  .bullets {{ margin:0; padding-left:20px; }}
  .bullets li {{ margin:9px 0; }}
  .status-line {{ display:grid; grid-template-columns:160px 1fr; gap:14px; margin:12px 0; }}
  .tag {{ display:inline-block; background:#eff6ff; color:#1d4ed8; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:700; }}
  .footer {{ max-width:1180px; margin:36px auto 0; color:#64748b; font-size:13px; }}
  @media (max-width: 900px) {{ .metrics,.grid,.story {{ grid-template-columns:1fr; }} .pie-row {{ grid-template-columns:1fr; }} .bar-row {{ grid-template-columns:1fr; gap:5px; }} .hero {{ min-height:76vh; }} }}
</style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <div class="eyebrow">EPO Markush Benchmark Collection</div>
      <h1>3000+ EPO样本抓取情况</h1>
      <p>当前集合共 {total} 个EP样本。主采集链路成功拿到了大规模原始公开材料，但用于审查评分报告的 file wrapper 闭环仍需要补抓 Register doclist 和审查过程 PDF。</p>
      <div class="metrics">
        <div class="metric"><strong>{total}</strong><span>总样本数</span></div>
        <div class="metric"><strong>{summary['complete_for_user_pdf_ocr_pipeline']}</strong><span>原始PDF/OCR口径成功</span></div>
        <div class="metric"><strong>{min_complete}</strong><span>审查报告闭环完整</span></div>
        <div class="metric"><strong>{label_counts['授权/授权公开样本']}</strong><span>授权/授权公开样本</span></div>
      </div>
    </div>
  </header>

  <main>
    <section class="section-title"><h2>一页结论</h2><p>最重要的是区分“原始公开PDF采集成功”和“审查案卷完整”。二者不是同一个成功率。</p></section>
    <section class="story">
      <div class="note-card">
        <h3>当前状态</h3>
        <ul class="bullets">
          <li>样本池规模已经达到 <b>{total}</b> 个，覆盖肿瘤、抗结核/抗菌、抗真菌等方向。</li>
          <li>Publication Server 链路表现稳定，按旧的 PDF/OCR 数据集口径，<b>{summary['complete_for_user_pdf_ocr_pipeline']}</b> 个样本已可继续抽文本、OCR或裁 Markush 图。</li>
          <li>用于可信审查评分报告的完整闭环较少：按 Register 三件套 + docs PDF + original PDF 校验，当前 <b>{min_complete}</b> 个完整。</li>
          <li>核心缺口不是“没有原始公开PDF”，而是多数样本尚未抓取 Register doclist 和 EPO file wrapper 审查过程 PDF。</li>
        </ul>
      </div>
      <div class="note-card">
        <h3>关键口径</h3>
        <div class="status-line"><b>原始公开口径</b><span><span class="tag">A/C/D</span> 有原始公开PDF或文本，可用于结构/claim/说明书提取。</span></div>
        <div class="status-line"><b>报告闭环口径</b><span><span class="tag">严格</span> 必须有 register、docs、original-application 三类原始材料。</span></div>
        <div class="status-line"><b>失败主因</b><span>Register GUI 批量抓取容易遇到 challenge / RobotAbuse，且当前批量链路偏 Publication Server-only。</span></div>
      </div>
    </section>

    <section class="section-title"><h2>饼图总览</h2><p>以下图表均为静态自包含图，可直接用于组会投屏。</p></section>
    <section class="grid">{''.join(charts)}</section>

    <section class="section-title"><h2>关键词与缺口</h2><p>关键词来自备份候选 manifest；缺口来自重新审计后的原始文件完整性表。</p></section>
    <section class="wide">{make_bars('Top 关键词组分布', '按 keyword_group 统计，保留Top 14，其余合并。', keyword_items)}</section>
    <section class="wide">{make_bars('Top 检索查询词', '按 source_query / matched_query 统计，反映样本来源查询。', query_items)}</section>
    <section class="wide">{make_bars('不完整样本的主要缺口', '同一个case可能同时命中多个缺口，因此这里是缺口发生次数，不是case数。', issue_items)}</section>

    <section class="section-title"><h2>为什么没有全抓成功</h2><p>这不是一个单点bug，而是采集目标和可访问性的共同结果。</p></section>
    <section class="story">
      <div class="note-card">
        <h3>原因拆解</h3>
        <ul class="bullets">
          <li><b>采集链路偏原始公开PDF：</b>当前大规模成功来自 EPO Publication Server，适合抓 A1/A2/B1 公开文本，但不提供完整审查往来材料。</li>
          <li><b>Register file wrapper 更难抓：</b>审查过程PDF依赖 Register doclist 页面和 documentId，批量访问容易触发 EPO challenge / RobotAbuse。</li>
          <li><b>旧complete口径偏宽：</b>D_pdf_only/C_pdf_text 被归为 complete，但它们并不等于审查报告闭环完整。</li>
          <li><b>skip逻辑会跳过补抓：</b>只要已有 original PDF，部分批处理会认为 fetched artifacts 存在，从而不再补 register/docs。</li>
        </ul>
      </div>
      <div class="note-card">
        <h3>下一步改法</h3>
        <ul class="bullets">
          <li>把完整性判定改为三段硬标准：Register 三件套、docs有效PDF、original有效PDF。</li>
          <li>新增 backfill manifest：分别列出 needs_register、needs_doclist、needs_docs_pdf、needs_original_pdf。</li>
          <li>修正 skip-existing：不能因为有 original PDF 就跳过 docs/register 补抓。</li>
          <li>Register 抓取低并发、断点续跑，并优先尝试 Zip Archive / selected documents 减少请求数。</li>
          <li>用 OPS / Register XML 稳定补元数据；PDF本体仍走 Register file inspection。</li>
        </ul>
      </div>
    </section>

    <section class="footer">
      数据来源：markush-run/benchmark/collection-index.json；markush-run/benchmark/patents/_raw_file_completeness_audit.csv；备份候选 manifest ep_application_candidates_overnight_20260703-175215_merged.json。
    </section>
  </main>
</body>
</html>"""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(document, encoding="utf-8")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()

