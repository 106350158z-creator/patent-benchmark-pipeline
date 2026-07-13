export type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

export type ReviewStatus =
  | "pending"
  | "verified"
  | "rejected"
  | "not_applicable"
  | "corrected_verified";

export type OverallStatus = "pending" | "needs_correction" | "success";

export interface SourceFingerprint {
  analysis_sha256: string;
  benchmark_input_sha256: string;
}

export interface ReviewItem {
  id: string;
  section: string;
  label: string;
  originalValue: JsonValue | undefined;
  kind: "text" | "number" | "record" | "image" | "claim" | "resource";
  evidencePaths: string[];
  missingRequired?: boolean;
  missingResources?: string[];
}

export interface NoteImage {
  name: string;
  mime: string;
  data_url: string;
  added_at: string;
}

export interface FieldReview {
  status: ReviewStatus;
  original_value: JsonValue | undefined;
  corrected_value: JsonValue | null;
  comment: string;
  note: string;
  note_images: NoteImage[];
  evidence_paths: string[];
  updated_at: string;
}

export interface ReviewDocument {
  schema_version: "1.0";
  application_number: string;
  source_fingerprint: SourceFingerprint;
  overall_status: OverallStatus;
  progress: {
    completed: number;
    total: number;
  };
  fields: Record<string, FieldReview>;
  updated_at: string;
}

export interface PatentCase {
  id: string;
  applicationNumber: string;
  category: string;
  relativePath: string;
  directoryHandle?: FileSystemDirectoryHandle;
  files?: Map<string, File>;
  analysis: JsonObject;
  benchmarkInput?: JsonObject;
  claimsVerified?: JsonObject;
  htmlName?: string;
  fingerprint: SourceFingerprint;
  reviewItems: ReviewItem[];
  candidateImagePaths: string[];
  sourceChanged: boolean;
}

export interface ViewerResource {
  path: string;
  page?: number;
  title?: string;
}

declare global {
  interface Window {
    showDirectoryPicker?: (options?: { mode?: "read" | "readwrite" }) => Promise<FileSystemDirectoryHandle>;
  }

  interface FileSystemHandle {
    queryPermission(descriptor?: { mode?: "read" | "readwrite" }): Promise<PermissionState>;
    requestPermission(descriptor?: { mode?: "read" | "readwrite" }): Promise<PermissionState>;
  }

  interface HTMLInputElement {
    webkitdirectory: boolean;
  }

  interface File {
    readonly webkitRelativePath: string;
  }
}
