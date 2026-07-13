import { buildReviewItems, candidateImagePaths } from "./review";
import type { JsonObject, PatentCase, ReviewItem, SourceFingerprint } from "./types";

const CORE_SUFFIXES = {
  analysis: "-analysis.json",
  benchmark: "-benchmark-input.json",
  claims: "-claims-verified.json",
  html: "-analysis.html",
};

function normalizePath(value: string): string {
  return value.replaceAll("\\", "/").replace(/^\.\//, "").replace(/^\/+/, "");
}

async function readJson(handle?: FileSystemFileHandle): Promise<JsonObject | undefined> {
  if (!handle) return undefined;
  const file = await handle.getFile();
  return JSON.parse(await file.text()) as JsonObject;
}

function fallbackHash(bytes: Uint8Array): string {
  let h1 = 0x811c9dc5;
  let h2 = 0x1000193;
  for (let i = 0; i < bytes.length; i += 1) {
    h1 = (h1 ^ bytes[i]) >>> 0;
    h1 = Math.imul(h1, 0x01000193) >>> 0;
    h2 = (h2 + bytes[i]) >>> 0;
    h2 = Math.imul(h2, 0x85ebca6b) >>> 0;
  }
  return `f${h1.toString(16).padStart(8, "0")}${h2.toString(16).padStart(8, "0")}-${bytes.length}`;
}

async function digestBuffer(buffer: ArrayBuffer): Promise<string> {
  if (crypto?.subtle) {
    try {
      const digest = await crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    } catch {
      // Non-secure context (file://) — fall back below.
    }
  }
  return fallbackHash(new Uint8Array(buffer));
}

async function sha256(handle?: FileSystemFileHandle): Promise<string> {
  if (!handle) return "";
  const file = await handle.getFile();
  return digestBuffer(await file.arrayBuffer());
}

async function sha256File(file?: File): Promise<string> {
  if (!file) return "";
  return digestBuffer(await file.arrayBuffer());
}

async function entries(handle: FileSystemDirectoryHandle): Promise<Array<[string, FileSystemHandle]>> {
  const result: Array<[string, FileSystemHandle]> = [];
  for await (const entry of handle.entries()) result.push(entry);
  return result;
}

async function findCaseDirectories(
  handle: FileSystemDirectoryHandle,
  segments: string[],
  found: Array<{ handle: FileSystemDirectoryHandle; segments: string[]; analysisName: string }>,
) {
  const children = await entries(handle);
  const analysis = children.find(([name, child]) => child.kind === "file" && name.endsWith(CORE_SUFFIXES.analysis));
  if (analysis) {
    found.push({ handle, segments, analysisName: analysis[0] });
    return;
  }
  for (const [name, child] of children) {
    if (child.kind !== "directory" || ["assets", "docs", "original-application", "prior-art", "register"].includes(name)) continue;
    await findCaseDirectories(child as FileSystemDirectoryHandle, [...segments, name], found);
  }
}

async function directFiles(handle: FileSystemDirectoryHandle): Promise<Map<string, FileSystemFileHandle>> {
  const files = new Map<string, FileSystemFileHandle>();
  for (const [name, child] of await entries(handle)) {
    if (child.kind === "file") files.set(name, child as FileSystemFileHandle);
  }
  return files;
}

function findBySuffix(files: Map<string, FileSystemFileHandle>, suffix: string): FileSystemFileHandle | undefined {
  return [...files.entries()].find(([name]) => name.endsWith(suffix))?.[1];
}

export async function resolveFile(
  caseHandle: FileSystemDirectoryHandle,
  rawPath: string,
): Promise<FileSystemFileHandle | undefined> {
  const normalized = normalizePath(rawPath);
  if (!normalized) return undefined;
  const attempts = [normalized];
  if (!normalized.includes("/")) {
    attempts.push(`docs/${normalized}`, `original-application/${normalized}`, `prior-art/${normalized}`, `assets/${normalized}`);
  }
  for (const attempt of attempts) {
    try {
      const parts = attempt.split("/").filter(Boolean);
      let current = caseHandle;
      for (const part of parts.slice(0, -1)) current = await current.getDirectoryHandle(part);
      return await current.getFileHandle(parts.at(-1)!);
    } catch {
      // Try the next common case-relative location.
    }
  }
  return undefined;
}

async function annotateMissingResources(caseHandle: FileSystemDirectoryHandle, items: ReviewItem[]): Promise<ReviewItem[]> {
  return Promise.all(items.map(async (reviewItem) => {
    const requiredResources = reviewItem.evidencePaths.filter((path) =>
      reviewItem.kind === "image" || /\.pdf$/i.test(path),
    );
    const missing: string[] = [];
    for (const path of requiredResources) {
      if (!await resolveFile(caseHandle, path)) missing.push(path);
    }
    return missing.length ? { ...reviewItem, missingResources: missing } : reviewItem;
  }));
}

function applicationNumber(analysis: JsonObject, analysisName: string): string {
  const meta = analysis.meta && typeof analysis.meta === "object" && !Array.isArray(analysis.meta)
    ? analysis.meta as JsonObject
    : {};
  const fromMeta = typeof meta.application_number === "string" ? meta.application_number.split(".")[0] : "";
  return fromMeta || analysisName.replace(CORE_SUFFIXES.analysis, "");
}

export async function importCases(
  rootHandle: FileSystemDirectoryHandle,
  onProgress?: (message: string) => void,
): Promise<PatentCase[]> {
  const directories: Array<{ handle: FileSystemDirectoryHandle; segments: string[]; analysisName: string }> = [];
  await findCaseDirectories(rootHandle, [], directories);
  const cases: PatentCase[] = [];

  for (let index = 0; index < directories.length; index += 1) {
    const found = directories[index];
    onProgress?.(`正在读取 ${index + 1}/${directories.length}：${found.analysisName}`);
    const files = await directFiles(found.handle);
    const analysisHandle = files.get(found.analysisName)!;
    const benchmarkHandle = findBySuffix(files, CORE_SUFFIXES.benchmark);
    const claimsHandle = findBySuffix(files, CORE_SUFFIXES.claims);
    const htmlHandle = findBySuffix(files, CORE_SUFFIXES.html);
    const analysis = (await readJson(analysisHandle))!;
    const benchmarkInput = await readJson(benchmarkHandle);
    const claimsVerified = await readJson(claimsHandle);
    const app = applicationNumber(analysis, found.analysisName);
    const fingerprint: SourceFingerprint = {
      analysis_sha256: await sha256(analysisHandle),
      benchmark_input_sha256: await sha256(benchmarkHandle),
    };
    const reviewItems = await annotateMissingResources(
      found.handle,
      buildReviewItems(analysis, benchmarkInput, claimsVerified),
    );
    const category = found.segments.length > 1 ? found.segments.at(-2)! : "未分类";
    cases.push({
      id: app,
      applicationNumber: app,
      category,
      relativePath: found.segments.join("/"),
      directoryHandle: found.handle,
      analysis,
      benchmarkInput,
      claimsVerified,
      htmlName: htmlHandle?.name,
      fingerprint,
      reviewItems,
      candidateImagePaths: candidateImagePaths(benchmarkInput),
      sourceChanged: false,
    });
  }
  return cases.sort((a, b) => a.applicationNumber.localeCompare(b.applicationNumber));
}

export async function importCasesFromFiles(
  fileList: FileList | File[],
  onProgress?: (message: string) => void,
): Promise<PatentCase[]> {
  const all = Array.from(fileList);
  const groups = new Map<string, { segments: string[]; analysisName: string; files: Map<string, File> }>();

  for (const file of all) {
    const rel = normalizePath(file.webkitRelativePath || file.name);
    const parts = rel.split("/").filter(Boolean);
    if (parts.length < 2) continue;
    const fileName = parts.at(-1)!;
    if (!fileName.endsWith(CORE_SUFFIXES.analysis)) continue;
    const caseSegments = parts.slice(0, -1);
    const caseKey = caseSegments.join("/");
    if (!groups.has(caseKey)) {
      groups.set(caseKey, { segments: caseSegments, analysisName: fileName, files: new Map() });
    }
  }

  const caseKeys = [...groups.keys()].sort((a, b) => b.length - a.length);
  for (const file of all) {
    const rel = normalizePath(file.webkitRelativePath || file.name);
    const owner = caseKeys.find((key) => rel === key || rel.startsWith(`${key}/`));
    if (!owner) continue;
    const relative = rel.slice(owner.length).replace(/^\/+/, "");
    if (relative) groups.get(owner)!.files.set(relative, file);
  }

  const readFileJson = async (file?: File): Promise<JsonObject | undefined> =>
    file ? (JSON.parse(await file.text()) as JsonObject) : undefined;

  const cases: PatentCase[] = [];
  const groupList = [...groups.values()];
  for (let index = 0; index < groupList.length; index += 1) {
    const group = groupList[index];
    onProgress?.(`正在读取 ${index + 1}/${groupList.length}：${group.analysisName}`);
    const analysisFile = group.files.get(group.analysisName);
    const benchmarkFile = [...group.files.entries()].find(([name]) => name.endsWith(CORE_SUFFIXES.benchmark))?.[1];
    const claimsFile = [...group.files.entries()].find(([name]) => name.endsWith(CORE_SUFFIXES.claims))?.[1];
    const htmlEntry = [...group.files.entries()].find(([name]) => name.endsWith(CORE_SUFFIXES.html));
    const analysis = (await readFileJson(analysisFile))!;
    const benchmarkInput = await readFileJson(benchmarkFile);
    const claimsVerified = await readFileJson(claimsFile);
    const app = applicationNumber(analysis, group.analysisName);
    const fingerprint: SourceFingerprint = {
      analysis_sha256: await sha256File(analysisFile),
      benchmark_input_sha256: await sha256File(benchmarkFile),
    };
    const baseItems = buildReviewItems(analysis, benchmarkInput, claimsVerified);
    const reviewItems = baseItems.map((reviewItem) => {
      const requiredResources = reviewItem.evidencePaths.filter((path) =>
        reviewItem.kind === "image" || /\.pdf$/i.test(path),
      );
      const missing = requiredResources.filter((path) => {
        const normalized = normalizePath(path);
        const attempts = [normalized];
        if (!normalized.includes("/")) {
          attempts.push(`docs/${normalized}`, `original-application/${normalized}`, `prior-art/${normalized}`, `assets/${normalized}`);
        }
        return !attempts.some((attempt) => group.files.has(attempt));
      });
      return missing.length ? { ...reviewItem, missingResources: missing } : reviewItem;
    });
    const category = group.segments.length > 1 ? group.segments.at(-2)! : "未分类";
    cases.push({
      id: app,
      applicationNumber: app,
      category,
      relativePath: group.segments.join("/"),
      files: group.files,
      analysis,
      benchmarkInput,
      claimsVerified,
      htmlName: htmlEntry?.[0],
      fingerprint,
      reviewItems,
      candidateImagePaths: candidateImagePaths(benchmarkInput),
      sourceChanged: false,
    });
  }
  return cases.sort((a, b) => a.applicationNumber.localeCompare(b.applicationNumber));
}

export async function getResourceFile(patentCase: PatentCase, path: string): Promise<File | undefined> {
  if (patentCase.files) {
    const normalized = normalizePath(path);
    if (!normalized) return undefined;
    const attempts = [normalized];
    if (!normalized.includes("/")) {
      attempts.push(`docs/${normalized}`, `original-application/${normalized}`, `prior-art/${normalized}`, `assets/${normalized}`);
    }
    for (const attempt of attempts) {
      const found = patentCase.files.get(attempt);
      if (found) return found;
    }
    return undefined;
  }
  if (!patentCase.directoryHandle) return undefined;
  const handle = await resolveFile(patentCase.directoryHandle, path);
  return handle?.getFile();
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export async function inlineHtmlImages(patentCase: PatentCase, html: string): Promise<string> {
  const references = new Set<string>();
  const pattern = /<img\b[^>]*?\bsrc\s*=\s*(["'])(.*?)\1/gi;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(html)) !== null) {
    const raw = match[2].trim();
    if (!raw || /^(?:https?:|data:|blob:|#)/i.test(raw)) continue;
    references.add(raw);
  }
  if (!references.size) return html;

  const replacements = new Map<string, string>();
  await Promise.all([...references].map(async (raw) => {
    const file = await getResourceFile(patentCase, raw);
    if (file) replacements.set(raw, await fileToDataUrl(file));
  }));
  if (!replacements.size) return html;

  return html.replace(pattern, (whole, quote: string, raw: string) => {
    const dataUrl = replacements.get(raw.trim());
    return dataUrl ? whole.replace(`${quote}${raw}${quote}`, `${quote}${dataUrl}${quote}`) : whole;
  });
}
