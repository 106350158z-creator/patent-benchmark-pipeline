import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildReviewItems, candidateImagePaths } from "../src/review";
import type { JsonObject } from "../src/types";

function findFiles(root: string, suffix: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) files.push(...findFiles(path, suffix));
    else if (entry.name.endsWith(suffix)) files.push(path);
  }
  return files;
}

describe("疾病分类9个样例", () => {
  const sampleRoot = resolve(process.cwd(), "..", "疾病分类9个样例");

  it("contains nine compatible analysis cases", () => {
    const analyses = findFiles(sampleRoot, "-analysis.json");
    expect(analyses).toHaveLength(9);
    for (const analysisPath of analyses) {
      const analysis = JSON.parse(readFileSync(analysisPath, "utf8")) as JsonObject;
      expect(analysis).toHaveProperty("meta");
      expect(analysis).toHaveProperty("dimension_scores");
      expect(analysis).toHaveProperty("evidence_trace");
    }
  });

  it("builds review fields and relative Markush image paths for all samples", () => {
    const benchmarks = findFiles(sampleRoot, "-benchmark-input.json");
    expect(benchmarks).toHaveLength(9);
    for (const benchmarkPath of benchmarks) {
      const benchmark = JSON.parse(readFileSync(benchmarkPath, "utf8")) as JsonObject;
      const app = benchmarkPath.match(/EP\d+/)?.[0];
      const folder = resolve(benchmarkPath, "..");
      const analysis = JSON.parse(readFileSync(resolve(folder, `${app}-analysis.json`), "utf8")) as JsonObject;
      const claimsPath = resolve(folder, `${app}-claims-verified.json`);
      const claims = JSON.parse(readFileSync(claimsPath, "utf8")) as JsonObject;
      const items = buildReviewItems(analysis, benchmark, claims);
      expect(items.length).toBeGreaterThan(20);
      for (const path of candidateImagePaths(benchmark)) {
        expect(path.startsWith("assets/")).toBe(true);
        expect(/^[A-Za-z]:/.test(path)).toBe(false);
      }
    }
  });
});
