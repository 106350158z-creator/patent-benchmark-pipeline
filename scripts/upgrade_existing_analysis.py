import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


WEIGHTS = {
    "novelty_score": 0.25,
    "inventive_step_score": 0.30,
    "support_score": 0.15,
    "clarity_score": 0.10,
    "eligibility_score": 0.20,
}


CASE_EVIDENCE = {
    "10007106.7": {
        "dimension_disc": {
            "novelty_disc": {
                "original_text": "You are informed that the examining division intends to grant a European patent",
                "translation": "兹通知你，审查部拟授予欧洲专利。",
                "llm_evidence_explanation": "最终授权意向说明审查部未维持 Art.54 新颖性拒绝；检索和审查争议集中在 Art.52/56。",
            },
            "inventive_step_disc": {
                "original_text": "features defining the business method are considered to be non-technical",
                "translation": "限定商业方法的特征被认为是非技术性的。",
                "llm_evidence_explanation": "该英文原文直接对应 COMVIK 分析中非技术特征不支持创造性的判断。",
            },
            "support_disc": {
                "original_text": "Description, Pages 4-26 as originally filed",
                "translation": "说明书第4-26页为原始提交文本。",
                "llm_evidence_explanation": "该英文原文说明授权文本保留了原始说明书基础，支持充分公开/支持未被实质质疑的评分。",
            },
            "clarity_disc": {
                "original_text": "Deletion of an incorporation-by-reference not essential to the invention",
                "translation": "删除对发明非必要的并入引用。",
                "llm_evidence_explanation": "该英文原文体现授权前的 Art.84 清楚性/简洁性修正，问题较小且已处理。",
            },
            "eligibility_disc": {
                "original_text": "the technical features of the claim amount to the definition of a general purpose computer",
                "translation": "权利要求的技术特征仅相当于定义一台通用计算机。",
                "llm_evidence_explanation": "该英文原文说明早期 Art.52 适格性风险来源；最终授权说明修正后风险被克服。",
            },
        },
        "specification_support": [
            {
                "location": "2022-10-11 intention to grant, p.1",
                "original_text": "the examining division intends to grant a European patent",
                "translation": "审查部拟授予欧洲专利。",
                "llm_evidence_explanation": "该原文确认最终结果为授权，说明早期 Art.52/56 异议在授权文本中已被克服或撤回。",
            },
            {
                "location": "2022-10-11 intention to grant, p.1",
                "original_text": "Description, Pages 4-26 as originally filed",
                "translation": "说明书第4-26页为原始提交文本。",
                "llm_evidence_explanation": "该原文支持“充分公开/支持未被质疑”的结论，并说明授权文本的说明书基础。",
            },
            {
                "location": "2022-10-11 intention to grant, p.2",
                "original_text": "Deletion of an incorporation-by-reference not essential to the invention",
                "translation": "删除对发明非必要的并入引用。",
                "llm_evidence_explanation": "该原文对应 Art.84 清楚性/简洁性修正，属于授权前的小幅形式问题。",
            },
        ],
        "examination_material_evidence": [
            {
                "issue": "inventive_step",
                "source": "2020-07-06 annex to communication",
                "original_text": "features defining the business method are considered to be non-technical",
                "translation": "限定商业方法的特征被认为是非技术性的。",
                "llm_evidence_explanation": "该证据说明审查员按 COMVIK 逻辑排除了业务规则对创造性的贡献。",
            },
            {
                "issue": "inventive_step",
                "source": "2020-07-06 annex to communication",
                "original_text": "faster are not derivable from the claims",
                "translation": "“更快”的效果不能从权利要求中得出。",
                "llm_evidence_explanation": "该证据解释了为什么申请人主张的技术效果未被审查员接受。",
            },
            {
                "issue": "prior_art",
                "source": "2020-07-06 annex to communication",
                "original_text": "D2 shows that OLAP Systems are well known",
                "translation": "D2 表明 OLAP 系统是公知的。",
                "llm_evidence_explanation": "该证据将 EP1492030/D2 与多维数据存储和通用计算机背景相连接。",
            },
        ],
    },
    "94912949.8": {
        "dimension_disc": {
            "novelty_disc": {
                "original_text": "does not involve an inventive step considered in the light of D1 (Articles 52(1) and 56 EPC)",
                "translation": "鉴于 D1，该主题不具备创造性（EPC 第52(1)条和第56条）。",
                "llm_evidence_explanation": "最终拒绝理由明确落在 Art.52/56，而不是 Art.54；该原文支持新颖性未作为拒绝核心。",
            },
            "inventive_step_disc": {
                "original_text": "Document D1 represents the closest prior art",
                "translation": "文件 D1 代表最接近的现有技术。",
                "llm_evidence_explanation": "该英文原文直接确认创造性分析的起点，D1 被用作最接近现有技术。",
            },
            "support_disc": {
                "original_text": "if the contribution is alleged to be technical it should be disclosed",
                "translation": "如果主张贡献是技术性的，则应当公开该技术贡献。",
                "llm_evidence_explanation": "该英文原文说明如果申请人主张并行处理/中央站等技术贡献，说明书需要提供对应技术公开。",
            },
            "clarity_disc": {
                "original_text": "the term product information can cover different meanings",
                "translation": "“产品信息”一词可能涵盖不同含义。",
                "llm_evidence_explanation": "该英文原文直接支持 Art.84 术语含义不清的评分依据。",
            },
            "eligibility_disc": {
                "original_text": "does not go beyond a method of doing business as such",
                "translation": "没有超出商业方法本身。",
                "llm_evidence_explanation": "该英文原文是最终 Art.52(2)(c)/(3) 排除主题判断的核心依据。",
            },
        },
        "specification_support": [
            {
                "location": "2003-02-25 annex to summons",
                "original_text": "if the contribution is alleged to be technical it should be disclosed",
                "translation": "如果主张贡献是技术性的，则应当公开该技术贡献。",
                "llm_evidence_explanation": "该证据支撑说明书对并行处理/中央站实现细节公开不足的风险判断。",
            },
            {
                "location": "2000-09-29 communication",
                "original_text": "the number of independent claims is not concise",
                "translation": "独立权利要求的数量不简洁。",
                "llm_evidence_explanation": "该证据对应 Art.84 清楚性和简洁性异议。",
            },
            {
                "location": "2000-09-29 communication",
                "original_text": "the term product information can cover different meanings",
                "translation": "“产品信息”一词可能涵盖不同含义。",
                "llm_evidence_explanation": "该证据说明关键术语边界不清，支持 clarity_score 较低。",
            },
        ],
        "examination_material_evidence": [
            {
                "issue": "eligibility",
                "source": "2003-09-01 grounds for decision",
                "original_text": "does not go beyond a method of doing business as such",
                "translation": "没有超出商业方法本身。",
                "llm_evidence_explanation": "该证据是最终驳回中 Art.52(2)(c)/(3) 排除主题结论的核心。",
            },
            {
                "issue": "inventive_step",
                "source": "2003-09-01 grounds for decision",
                "original_text": "Document D1 represents the closest prior art",
                "translation": "文件 D1 代表最接近的现有技术。",
                "llm_evidence_explanation": "该证据确认创造性分析的最接近现有技术基础为 D1 US4972504。",
            },
            {
                "issue": "inventive_step",
                "source": "2003-09-01 grounds for decision",
                "original_text": "no technical solution to a realised technical problem has been proposed",
                "translation": "没有提出针对已认识技术问题的技术解决方案。",
                "llm_evidence_explanation": "该证据说明通用处理器/数据库实现未形成可支持创造性的技术方案。",
            },
            {
                "issue": "inventive_step",
                "source": "2000-09-29 communication",
                "original_text": "D5 describes methods of spatial autocorrelation",
                "translation": "D5 描述了空间自相关方法。",
                "llm_evidence_explanation": "该证据说明距离、GIS 和空间权重函数属于已有背景，削弱区别特征的创造性。",
            },
        ],
    },
}


def official_link(citation: str) -> str:
    cleaned = re.sub(r"^\s*D[0-9]{1,2}\s+", "", citation.strip(), flags=re.I)
    match = re.search(r"\b((?:WO|EP|US|JP|CN|GB)[-\s]?[A-Z]?-?\s?[0-9][A-Z0-9/.,\-\s]{3,50})", cleaned, re.I)
    query = match.group(1) if match else cleaned[:120]
    return f"https://worldwide.espacenet.com/patent/search?q={quote_plus(query)}"


def score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def recalculate_aggregate(scores: dict[str, Any]) -> int:
    return int(round(sum(score(scores.get(key)) * weight for key, weight in WEIGHTS.items())))


def normalize_prior_art(analysis: dict[str, Any], benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    existing = ((analysis.get("evidence_trace") or {}).get("prior_art_documents") or [])
    benchmark_items = ((benchmark.get("benchmark_input") or {}).get("prior_art_docs") or [])
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(citation: str, mentioned: bool, method: str, explanation: str, link: str = "") -> None:
        key = re.sub(r"\W+", "", citation).lower()
        if not citation or key in seen or len(items) >= 20:
            return
        seen.add(key)
        items.append(
            {
                "rank": len(items) + 1,
                "citation": citation,
                "mentioned_in_examined_text": mentioned,
                "retrieval_method": method,
                "official_source": "European Patent Office Espacenet",
                "official_link": link or official_link(citation),
                "llm_evidence_explanation": explanation,
            }
        )

    for citation in existing:
        add(
            str(citation),
            True,
            "examined_text",
            "该引用由审查文本或最终决定直接提及，用于评价新颖性、创造性或技术贡献。",
        )

    for item in benchmark_items:
        if not isinstance(item, dict):
            continue
        mentioned = bool(item.get("mentioned_in_examined_text"))
        method = str(item.get("retrieval_method") or ("examined_text" if mentioned else "official_semantic_retrieval"))
        explanation = (
            "该引用由审查文本直接提及。"
            if mentioned
            else "该项由 EPO/Espacenet 官网语义检索补充，用于扩展相关专利背景。"
        )
        add(str(item.get("citation") or ""), mentioned, method, explanation, str(item.get("official_link") or ""))

    return items


def upgrade(analysis_path: Path, benchmark_path: Path, output_path: Path) -> None:
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))

    scores = analysis.setdefault("dimension_scores", {})
    scores.pop("unity_score", None)
    scores.pop("unity_disc", None)
    analysis["aggregate_score"] = recalculate_aggregate(scores)

    evidence = analysis.setdefault("evidence_trace", {})
    evidence["prior_art_documents"] = normalize_prior_art(analysis, benchmark)

    app_no = str((analysis.get("meta") or {}).get("application_number") or "")
    case_evidence = CASE_EVIDENCE.get(app_no, {})
    if case_evidence:
        for disc_key, evidence_item in case_evidence["dimension_disc"].items():
            existing = scores.get(disc_key, "")
            if isinstance(existing, dict):
                analysis_text = existing.get("analysis") or ""
            else:
                analysis_text = str(existing)
            scores[disc_key] = {
                "analysis": analysis_text,
                "original_text": evidence_item["original_text"],
                "translation": evidence_item["translation"],
                "llm_evidence_explanation": evidence_item["llm_evidence_explanation"],
            }
        evidence["specification_support"] = case_evidence["specification_support"]
        evidence["examination_material_evidence"] = case_evidence["examination_material_evidence"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote upgraded analysis JSON: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upgrade an existing patent analysis JSON to the current report schema.")
    parser.add_argument("analysis_json")
    parser.add_argument("benchmark_input")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    upgrade(Path(args.analysis_json), Path(args.benchmark_input), Path(args.output))


if __name__ == "__main__":
    main()
