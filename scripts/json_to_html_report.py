import argparse
import json
from html import escape
from pathlib import Path
from typing import Any


DIMENSIONS = [
    ("novelty", "新颖性", "novelty_score", "novelty_disc"),
    ("inventive_step", "创造性", "inventive_step_score", "inventive_step_disc"),
    ("support", "充分公开/支持", "support_score", "support_disc"),
    ("clarity", "清楚性", "clarity_score", "clarity_disc"),
    ("unity", "单一性", "unity_score", "unity_disc"),
    ("eligibility", "适格性", "eligibility_score", "eligibility_disc"),
]


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
    return "<ul>" + "".join(f"<li>{h(item)}</li>" for item in items) + "</ul>"


def render_meta(meta: dict[str, Any], grant_label: str, aggregate_score: Any) -> str:
    rows = [
        ("法域", meta.get("jurisdiction")),
        ("申请号", meta.get("application_number")),
        ("发明名称", meta.get("title")),
        ("申请人", meta.get("applicant")),
        ("申请日", meta.get("filing_date")),
        ("审查意见日期", meta.get("examination_date")),
        ("结果", meta.get("outcome")),
        ("是否授权", grant_label),
    ]
    body = "".join(f"<tr><th>{h(label)}</th><td>{h(value)}</td></tr>" for label, value in rows)
    return f"""
    <section class="grid">
      <div class="card score-card {score_class(aggregate_score)}">
        <div class="label">综合评分</div>
        <div class="big-score">{h(aggregate_score)}</div>
        <div class="score-bar"><span style="width:{pct(aggregate_score)}%"></span></div>
      </div>
      <div class="card">
        <h2>基本信息</h2>
        <table class="kv">{body}</table>
      </div>
    </section>
    """


def render_dimensions(scores: dict[str, Any]) -> str:
    rows = []
    for _, label, score_key, disc_key in DIMENSIONS:
        score = scores.get(score_key, "")
        rows.append(
            "<tr>"
            f"<td class='dim-name'>{h(label)}</td>"
            f"<td><span class='pill {score_class(score)}'>{h(score)}</span></td>"
            f"<td>{h(scores.get(disc_key, '未被质疑'))}</td>"
            "</tr>"
        )
    return f"""
    <section class="card">
      <h2>维度评分</h2>
      <table>
        <thead><tr><th>维度</th><th>分数</th><th>分析理由</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
    """


def render_evidence(evidence: dict[str, Any]) -> str:
    support = evidence.get("specification_support") or []
    support_rows = []
    for item in support:
        if isinstance(item, dict):
            support_rows.append(
                "<tr>"
                f"<td>{h(item.get('location'))}</td>"
                f"<td>{h(item.get('relevance'))}</td>"
                "</tr>"
            )
        else:
            support_rows.append(f"<tr><td colspan='2'>{h(item)}</td></tr>")

    if not support_rows:
        support_rows.append("<tr><td colspan='2' class='muted'>无</td></tr>")

    return f"""
    <section class="card">
      <h2>证据链</h2>
      <table class="kv">
        <tr><th>引用先文</th><td>{render_list(evidence.get('prior_art_documents'))}</td></tr>
        <tr><th>受影响权利要求</th><td>{h(evidence.get('affected_claims'))}</td></tr>
        <tr><th>审查轮次</th><td>{h(evidence.get('examination_rounds'))}</td></tr>
      </table>
      <h3>说明书/审查证据位置</h3>
      <table>
        <thead><tr><th>位置</th><th>关联说明</th></tr></thead>
        <tbody>{''.join(support_rows)}</tbody>
      </table>
    </section>
    """


def render_html(data: dict[str, Any], source_name: str) -> str:
    meta = data.get("meta") or {}
    title = meta.get("title") or source_name
    app_no = meta.get("application_number") or ""
    aggregate = data.get("aggregate_score", "")
    risks = data.get("top_risk_reasons") or []
    actions = data.get("recommended_actions") or []
    evidence = data.get("evidence_trace") or {}

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{h(app_no)} 专利审查分析报告</title>
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
    .columns {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }}
    .muted {{
      color: var(--muted);
    }}
    .source {{
      margin-top: 8px;
      font-size: 13px;
      color: var(--muted);
    }}
    @media (max-width: 820px) {{
      .grid, .columns {{
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
      <p class="subtitle">{h(app_no)} · 专利审查 JSON 可视化报告</p>
      <p class="source">来源 JSON：{h(source_name)}</p>
    </header>

    {render_meta(meta, data.get("grant_label", ""), aggregate)}
    {render_dimensions(data.get("dimension_scores") or {})}

    <section class="columns">
      <div class="card">
        <h2>主要风险</h2>
        {render_list(risks)}
      </div>
      <div class="card">
        <h2>建议动作</h2>
        {render_list(actions)}
      </div>
    </section>

    {render_evidence(evidence)}
  </main>
</body>
</html>
"""


def convert(input_path: Path, output_path: Path | None) -> Path:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if output_path is None:
        output_path = input_path.with_suffix(".html")
    html = render_html(data, input_path.name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8", newline="\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert patent analysis JSON to a standalone HTML report.")
    parser.add_argument("input_json", help="Path to the final patent analysis JSON.")
    parser.add_argument("-o", "--output", help="Output HTML path. Defaults to input path with .html suffix.")
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output) if args.output else None
    result = convert(input_path, output_path)
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()
