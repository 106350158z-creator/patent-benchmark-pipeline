import type {
  FieldReview,
  JsonObject,
  JsonValue,
  OverallStatus,
  PatentCase,
  ReviewDocument,
  ReviewItem,
  ReviewStatus,
  SourceFingerprint,
} from "./types";

const META_FIELDS = [
  ["jurisdiction", "法域"],
  ["application_number", "申请号"],
  ["title", "发明名称"],
  ["applicant", "申请人"],
  ["filing_date", "申请日"],
  ["examination_date", "审查意见日期"],
  ["outcome", "授权结果"],
] as const;

const DIMENSIONS = [
  ["novelty", "新颖性"],
  ["inventive_step", "创造性"],
  ["support", "充分公开 / 支持"],
  ["clarity", "清楚性"],
  ["eligibility", "适格性"],
] as const;

function objectValue(value: JsonValue | undefined): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function arrayValue(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function evidencePaths(value: JsonValue | undefined): string[] {
  if (typeof value === "string") {
    return /\.(pdf|txt|html?|png|jpe?g|webp)$/i.test(value) ? [value] : [];
  }
  if (Array.isArray(value)) return value.flatMap((child) => evidencePaths(child));
  if (!value || typeof value !== "object") return [];
  const record = value as JsonObject;
  const keys = ["source", "pdf", "source_pdf", "draft_text_pdf", "image_path", "path", "local_pdf"];
  return [...new Set(keys.flatMap((key) => evidencePaths(record[key])))];
}

function item(
  id: string,
  section: string,
  label: string,
  originalValue: JsonValue | undefined,
  kind: ReviewItem["kind"] = "text",
  required = true,
): ReviewItem {
  return {
    id,
    section,
    label,
    originalValue,
    kind,
    evidencePaths: evidencePaths(originalValue),
    missingRequired: required && (originalValue === undefined || originalValue === null || originalValue === ""),
  };
}

function pushArrayItems(
  target: ReviewItem[],
  values: JsonValue | undefined,
  path: string,
  section: string,
  singularLabel: string,
  kind: ReviewItem["kind"] = "record",
) {
  const rows = arrayValue(values);
  if (!rows.length) {
    target.push(item(path, section, singularLabel, undefined, kind, true));
    return;
  }
  rows.forEach((row, index) => {
    target.push(item(`${path}/${index}`, section, `${singularLabel} ${index + 1}`, row, kind, true));
  });
}

export function buildReviewItems(
  analysis: JsonObject,
  benchmarkInput?: JsonObject,
  claimsVerified?: JsonObject,
): ReviewItem[] {
  const items: ReviewItem[] = [];
  const meta = objectValue(analysis.meta);
  const scores = objectValue(analysis.dimension_scores);

  META_FIELDS.forEach(([key, label]) => {
    items.push(item(`/meta/${key}`, "案件元数据", label, meta[key]));
  });
  items.push(item("/grant_label", "案件元数据", "授权与否标签", analysis.grant_label));

  const input = objectValue(benchmarkInput?.benchmark_input);
  const drugStructure = objectValue(input.drug_structure);
  const selectedImages = arrayValue(drugStructure.markush_images);
  if (!selectedImages.length) {
    items.push(item(
      "/benchmark_input/drug_structure/markush_images",
      "申请预览与 Markush",
      "Markush 图片",
      undefined,
      "image",
      true,
    ));
  } else {
    selectedImages.forEach((image, index) => {
      const imageItem = item(
        `/benchmark_input/drug_structure/markush_images/${index}`,
        "申请预览与 Markush",
        `Markush 图片 ${index + 1}`,
        image,
        "image",
      );
      const path = stringValue(objectValue(image).image_path);
      imageItem.evidencePaths = path ? [path] : [];
      imageItem.missingRequired = !path;
      items.push(imageItem);
    });
  }

  const claimText = objectValue(input.claim_text);
  items.push(item(
    "/benchmark_input/claim_text/claim_1",
    "申请预览与 Markush",
    "权利要求 1 预览",
    claimText.claim_1,
    "text",
  ));

  DIMENSIONS.forEach(([key, label]) => {
    items.push(item(`/dimension_scores/${key}_score`, "审查维度评分", `${label}分数`, scores[`${key}_score`], "number"));
    items.push(item(`/dimension_scores/${key}_disc`, "审查维度评分", `${label}分析`, scores[`${key}_disc`], "text", false));
  });
  items.push(item("/aggregate_score", "审查维度评分", "综合评分", analysis.aggregate_score, "number"));

  pushArrayItems(items, analysis.top_risk_reasons, "/top_risk_reasons", "主要风险", "风险");
  pushArrayItems(items, analysis.recommended_actions, "/recommended_actions", "建议动作", "建议");

  const trace = objectValue(analysis.evidence_trace);
  items.push(item("/evidence_trace/examination_rounds", "证据链", "审查轮次", trace.examination_rounds, "number"));
  pushArrayItems(items, trace.affected_claims, "/evidence_trace/affected_claims", "证据链", "受影响权利要求");
  pushArrayItems(items, trace.prior_art_documents, "/evidence_trace/prior_art_documents", "引用先文", "引用先文");
  pushArrayItems(items, trace.specification_support, "/evidence_trace/specification_support", "说明书支持证据", "支持证据");
  pushArrayItems(items, trace.examination_material_evidence, "/evidence_trace/examination_material_evidence", "审查材料证据", "审查证据");
  pushArrayItems(items, analysis.risk_source_sentences, "/risk_source_sentences", "风险依据原文", "风险依据");
  pushArrayItems(items, analysis.action_basis_source_sentences, "/action_basis_source_sentences", "建议依据原文", "建议依据");

  const claims = arrayValue(claimsVerified?.claims);
  if (!claims.length) {
    items.push(item("/claims_verified/claims", "权利要求", "已核验权利要求", undefined, "claim", true));
  } else {
    claims.forEach((claim, index) => {
      const claimRecord = objectValue(claim);
      const number = claimRecord.claim_number ?? index + 1;
      items.push(item(`/claims_verified/claims/${index}`, "权利要求", `权利要求 ${number}`, claim, "claim"));
    });
  }

  return items;
}

export function candidateImagePaths(benchmarkInput?: JsonObject): string[] {
  const input = objectValue(benchmarkInput?.benchmark_input);
  const structure = objectValue(input.drug_structure);
  const arrays = [structure.markush_images, structure.markush_candidate_images, structure.markush_page_images];
  return [...new Set(arrays.flatMap(arrayValue).map((value) => stringValue(objectValue(value).image_path)).filter(Boolean))];
}

export function reviewStatus(review: ReviewDocument, items: ReviewItem[]): OverallStatus {
  const fields = items.map((reviewItem) => review.fields[reviewItem.id]);
  if (fields.some((field) => field?.status === "rejected")) return "needs_correction";
  const complete = items.every((reviewItem) => {
    const field = review.fields[reviewItem.id];
    if (!field) return false;
    const blocked = reviewItem.missingRequired || Boolean(reviewItem.missingResources?.length);
    if (blocked) return field.status === "corrected_verified";
    return ["verified", "corrected_verified", "not_applicable"].includes(field.status);
  });
  return complete && items.length > 0 ? "success" : "pending";
}

export function updateReviewSummary(review: ReviewDocument, items: ReviewItem[]): ReviewDocument {
  const completed = items.filter((reviewItem) => {
    const status = review.fields[reviewItem.id]?.status;
    if ((reviewItem.missingRequired || reviewItem.missingResources?.length) && status !== "corrected_verified") return false;
    return status === "verified" || status === "corrected_verified" || status === "not_applicable";
  }).length;
  return {
    ...review,
    overall_status: reviewStatus(review, items),
    progress: { completed, total: items.length },
    updated_at: new Date().toISOString(),
  };
}

export function createReview(
  applicationNumber: string,
  fingerprint: SourceFingerprint,
  items: ReviewItem[],
): ReviewDocument {
  const fields = Object.fromEntries(items.map((reviewItem) => [
    reviewItem.id,
    {
      status: "pending" as ReviewStatus,
      original_value: reviewItem.originalValue,
      corrected_value: null,
      comment: "",
      note: "",
      note_images: [],
      evidence_paths: reviewItem.evidencePaths,
      updated_at: new Date().toISOString(),
    } satisfies FieldReview,
  ]));
  return updateReviewSummary({
    schema_version: "1.0",
    application_number: applicationNumber,
    source_fingerprint: fingerprint,
    overall_status: "pending",
    progress: { completed: 0, total: items.length },
    fields,
    updated_at: new Date().toISOString(),
  }, items);
}

export function fingerprintMatches(a: SourceFingerprint, b: SourceFingerprint): boolean {
  return a.analysis_sha256 === b.analysis_sha256 && a.benchmark_input_sha256 === b.benchmark_input_sha256;
}

export function caseOverallStatus(patentCase: PatentCase, review?: ReviewDocument): OverallStatus {
  return review ? reviewStatus(review, patentCase.reviewItems) : "pending";
}
