# 专利审查报告 JSON 分析提示词模板

把下面提示词中的 `<...>` 内容替换为实际材料。建议至少提供：

- Register `main` 页面关键字段
- Register `legal` 页面状态
- 审查通信/附件 OCR 文本
- 最终授权决定或驳回决定 OCR 文本
- 引用先文列表

```text
你是专利审查报告分析专家。请根据以下欧洲专利审查材料，提取字段并严格输出 JSON，不要附加解释。

案件信息：
- jurisdiction: EP
- Register number: <EP_NUMBER>
- application number: <APPLICATION_NUMBER>
- title: <TITLE>
- applicant: <APPLICANT>
- filing date: <FILING_DATE>
- final status: <GRANTED/REFUSED/PENDING>
- relevant dates: <审查通信日期、Rule 71(3)日期、决定日期等>

引用先文：
<PRIOR_ART_LIST>

审查通信/附件 OCR 文本：
<OFFICE_ACTION_OCR_TEXT>

最终决定/授权文本 OCR 文本：
<FINAL_DECISION_OR_GRANT_TEXT>

请输出以下 JSON Schema：

{
  "meta": {
    "jurisdiction": "CN|EP|US",
    "application_number": "申请号",
    "title": "发明名称",
    "applicant": "申请人",
    "filing_date": "申请日",
    "examination_date": "审查意见发文日或最终决定日",
    "outcome": "granted|rejected|pending"
  },
  "grant_label": "yes|no",
  "dimension_scores": {
    "novelty_score": 0-100,
    "novelty_disc": "新颖性评价的具体理由，必须引用对比文件编号和区别特征；如未被质疑，写明未被质疑并说明引用文件主要用于哪些法条",
    "inventive_step_score": 0-100,
    "inventive_step_disc": "创造性评价的具体理由，包括最接近现有技术、区别特征、技术效果；EP 案件必须按 COMVIK 方法区分技术/非技术特征",
    "support_score": 0-100,
    "support_disc": "充分公开/Art.83 或说明书支持评价，哪些权利要求缺乏支持；未涉及则写未被质疑",
    "clarity_score": 0-100,
    "clarity_disc": "Art.84 清楚性评价，列出模糊用语或不简洁问题；未涉及则写未被质疑",
    "eligibility_score": 0-100,
    "eligibility_disc": "EP Art.52 专利适格性评价，说明是否属于 Art.52(2)(3) 排除主题"
  },
  "aggregate_score": 0-100,
  "top_risk_reasons": [
    "中文，每条不超过50字"
  ],
  "recommended_actions": [
    "具体可执行的修改或答复建议"
  ],
  "evidence_trace": {
    "prior_art_documents": [
      {
        "rank": 1,
        "citation": "所有引用的对比文件/先文，保留top20",
        "mentioned_in_examined_text": true,
        "official_link": "EPO/Espacenet官方链接",
        "llm_evidence_explanation": "LLM证据说明"
      }
    ],
    "affected_claims": [1,2,3],
    "specification_support": [
      {
        "location": "段落号/页码/通信页",
        "original_text": "审查材料原文，保持原语言",
        "translation": "中文翻译；如原文为中文，则给英文翻译",
        "llm_evidence_explanation": "支撑或风险说明"
      }
    ],
    "examination_material_evidence": [
      {
        "issue": "novelty|inventive_step|support|clarity|eligibility|outcome",
        "source": "文件名/页码/段落",
        "original_text": "审查材料原文，保持原语言",
        "translation": "中文翻译；如原文为中文，则给英文翻译",
        "llm_evidence_explanation": "LLM证据说明"
      }
    ],
    "examination_rounds": 1
  }
}

评分标准：
- 100分：该维度完全通过审查，无任何异议
- 80分：有小问题但已通过修改克服
- 50分：存在争议，结果不确定
- 10-20分：被明确否定，审查员给出了充分否定理由
- 0分：完全不满足，无救济可能

aggregate_score 权重：
- 新颖性 25%
- 创造性 30%
- 充分公开 15%
- 清楚性 10%
- 适格性 20%

注意：
1. EP 案件必须说明 COMVIK 逻辑：非技术业务规则不能支持创造性，只能作为技术问题约束。
2. 如果某维度未被审查员质疑，给 100 分，disc 写“未被质疑”，但仍需结合材料说明没有该异议。
3. top_risk_reasons 用中文，简明扼要。
4. recommended_actions 必须具体到可加入的技术特征或答复论点。
5. 不输出单一性 unity 相关评分字段。
6. evidence_trace 中证据必须保留原始文本原有语言，在旁边给翻译，并增加 LLM 证据说明。
7. 输出必须是合法 JSON。
```
