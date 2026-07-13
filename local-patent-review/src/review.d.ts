import type { JsonObject, OverallStatus, PatentCase, ReviewDocument, ReviewItem, SourceFingerprint } from "./types";
export declare function buildReviewItems(analysis: JsonObject, benchmarkInput?: JsonObject, claimsVerified?: JsonObject): ReviewItem[];
export declare function candidateImagePaths(benchmarkInput?: JsonObject): string[];
export declare function reviewStatus(review: ReviewDocument, items: ReviewItem[]): OverallStatus;
export declare function updateReviewSummary(review: ReviewDocument, items: ReviewItem[]): ReviewDocument;
export declare function createReview(applicationNumber: string, fingerprint: SourceFingerprint, items: ReviewItem[]): ReviewDocument;
export declare function fingerprintMatches(a: SourceFingerprint, b: SourceFingerprint): boolean;
export declare function caseOverallStatus(patentCase: PatentCase, review?: ReviewDocument): OverallStatus;
