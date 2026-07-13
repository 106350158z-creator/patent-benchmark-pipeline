import { describe, expect, it } from "vitest";
import { buildReviewItems, createReview, reviewStatus, updateReviewSummary } from "../src/review";
import type { JsonObject } from "../src/types";

const analysis: JsonObject = {
  meta: {
    jurisdiction: "EP",
    application_number: "EP12345678.0",
    title: "Example",
    applicant: "Applicant",
    filing_date: "2020-01-01",
    examination_date: "2024-01-01",
    outcome: "granted",
  },
  grant_label: "yes",
  dimension_scores: {
    novelty_score: 80,
    novelty_disc: "novelty",
    inventive_step_score: 70,
    inventive_step_disc: "inventive",
    support_score: 60,
    support_disc: "support",
    clarity_score: 90,
    clarity_disc: "clarity",
    eligibility_score: 100,
    eligibility_disc: "eligible",
  },
  aggregate_score: 80,
  top_risk_reasons: [{ original_text: "risk", source: "docs/risk.pdf" }],
  recommended_actions: [{ original_text: "action" }],
  evidence_trace: {
    prior_art_documents: [{ citation: "D1" }],
    affected_claims: [1],
    specification_support: [{ source: "docs/support.pdf" }],
    examination_material_evidence: [{ source: "docs/exam.pdf" }],
    examination_rounds: 2,
  },
  risk_source_sentences: [{ original_text: "risk sentence" }],
  action_basis_source_sentences: [{ original_text: "action sentence" }],
};

const benchmark: JsonObject = {
  benchmark_input: {
    drug_structure: {
      markush_images: [{ image_path: "assets/markush-candidates/markush.png" }],
      markush_candidate_images: [{ image_path: "assets/markush-candidates/candidate.png" }],
    },
    claim_text: { claim_1: "A compound..." },
  },
};

const claims: JsonObject = { claims: [{ claim_number: 1, text: "A compound..." }] };

describe("review model", () => {
  it("builds every report section including Markush and claims", () => {
    const items = buildReviewItems(analysis, benchmark, claims);
    expect(items.some((item) => item.kind === "image")).toBe(true);
    expect(items.some((item) => item.kind === "claim")).toBe(true);
    expect(new Set(items.map((item) => item.section))).toContain("审查维度评分");
    expect(items.length).toBeGreaterThan(25);
  });

  it("requires every field before success", () => {
    const items = buildReviewItems(analysis, benchmark, claims);
    let review = createReview("EP12345678", { analysis_sha256: "a", benchmark_input_sha256: "b" }, items);
    expect(reviewStatus(review, items)).toBe("pending");
    for (const item of items) review.fields[item.id].status = "verified";
    review = updateReviewSummary(review, items);
    expect(review.overall_status).toBe("success");
    expect(review.progress.completed).toBe(items.length);
  });

  it("does not allow a missing required resource to pass without a correction", () => {
    const items = buildReviewItems(analysis, benchmark, claims);
    items[0].missingResources = ["missing.pdf"];
    const review = createReview("EP12345678", { analysis_sha256: "a", benchmark_input_sha256: "b" }, items);
    for (const item of items) review.fields[item.id].status = "verified";
    expect(reviewStatus(review, items)).toBe("pending");
    review.fields[items[0].id].status = "corrected_verified";
    expect(reviewStatus(review, items)).toBe("success");
  });

  it("keeps rejected fields in needs_correction", () => {
    const items = buildReviewItems(analysis, benchmark, claims);
    const review = createReview("EP12345678", { analysis_sha256: "a", benchmark_input_sha256: "b" }, items);
    review.fields[items[0].id].status = "rejected";
    expect(reviewStatus(review, items)).toBe("needs_correction");
  });
});
