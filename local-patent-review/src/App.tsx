import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleHelp,
  Download,
  FileJson,
  FileSearch,
  FolderOpen,
  Image as ImageIcon,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { getResourceFile, importCases, importCasesFromFiles, inlineHtmlImages } from "./importer";
import {
  caseOverallStatus,
  createReview,
  fingerprintMatches,
  updateReviewSummary,
} from "./review";
import { loadReview, loadRootHandle, saveReview, saveRootHandle } from "./storage";
import type {
  FieldReview,
  JsonObject,
  JsonValue,
  OverallStatus,
  PatentCase,
  ReviewDocument,
  ReviewItem,
  ReviewStatus,
  ViewerResource,
} from "./types";

const STATUS_LABELS: Record<OverallStatus, string> = {
  pending: "待审核",
  needs_correction: "有问题",
  success: "成功",
};

const FIELD_STATUS_LABELS: Record<ReviewStatus, string> = {
  pending: "待审核",
  verified: "通过",
  rejected: "不通过",
  not_applicable: "不适用",
  corrected_verified: "修正后通过",
};

interface ViewerState extends ViewerResource {
  url?: string;
  srcdoc?: string;
  mime?: string;
  text?: string;
  error?: string;
}

function isRecord(value: JsonValue | undefined): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function downloadJson(name: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

function overallClass(status: OverallStatus) {
  return `status-pill status-${status}`;
}

function displayValue(value: JsonValue | undefined): string {
  if (value === undefined || value === null || value === "") return "未提供";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function compactRecord(value: JsonValue | undefined) {
  if (!isRecord(value)) return <div className="field-value prewrap">{displayValue(value)}</div>;
  return (
    <dl className="record-grid">
      {Object.entries(value).map(([key, child]) => (
        <div key={key} className="record-row">
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{displayValue(child)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ResourceImage({ patentCase, path, onOpen }: {
  patentCase: PatentCase;
  path: string;
  onOpen: (resource: ViewerResource) => void;
}) {
  const [url, setUrl] = useState("");
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    let objectUrl = "";
    getResourceFile(patentCase, path).then((file) => {
      if (!file) {
        setMissing(true);
        return;
      }
      objectUrl = URL.createObjectURL(file);
      setUrl(objectUrl);
    });
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [patentCase, path]);

  if (missing) return <div className="image-missing"><AlertTriangle size={18} />图片不存在：{path}</div>;
  if (!url) return <div className="image-loading">正在读取图片...</div>;
  return <button className="image-preview" onClick={() => onOpen({ path, title: "Markush 图片" })}><img src={url} alt="Markush 候选" loading="lazy" /></button>;
}

function EvidenceViewer({ viewer, onClose }: { viewer?: ViewerState; onClose: () => void }) {
  return (
    <aside className={`viewer ${viewer ? "viewer-open" : ""}`}>
      <div className="viewer-head">
        <div>
          <span className="eyebrow">证据查看器</span>
          <strong>{viewer?.title || viewer?.path || "尚未选择证据"}</strong>
        </div>
        {viewer && <button className="icon-button" title="关闭证据查看器" onClick={onClose}><X size={18} /></button>}
      </div>
      {!viewer && <div className="viewer-empty"><FileSearch size={34} /><p>点击字段中的来源路径、PDF 或图片，在这里核验。</p></div>}
      {viewer?.error && <div className="viewer-error"><AlertTriangle size={20} />{viewer.error}</div>}
      {viewer?.text !== undefined && <pre className="text-viewer">{viewer.text}</pre>}
      {viewer?.url && viewer.mime?.startsWith("image/") && <div className="viewer-image"><img src={viewer.url} alt={viewer.title || viewer.path} /></div>}
      {viewer?.url && viewer.mime === "application/pdf" && <iframe className="viewer-frame" src={`${viewer.url}${viewer.page ? `#page=${viewer.page}` : ""}`} title={viewer.title || viewer.path} />}
      {viewer?.srcdoc !== undefined && <iframe className="viewer-frame" srcDoc={viewer.srcdoc} sandbox="allow-same-origin" title={viewer.title || viewer.path} />}
    </aside>
  );
}

function ReviewControls({
  item,
  field,
  candidatePaths,
  onChange,
  onConfirmCorrection,
}: {
  item: ReviewItem;
  field: FieldReview;
  candidatePaths: string[];
  onChange: (patch: Partial<FieldReview>) => void;
  onConfirmCorrection: () => void;
}) {
  const blocked = Boolean(item.missingRequired || item.missingResources?.length);
  const rejectMode = field.status === "rejected";
  return (
    <div className="review-controls">
      <div className="review-actions" role="group" aria-label={`${item.label} 校验状态`}>
        <button disabled={blocked} className={field.status === "verified" ? "active pass" : ""} onClick={() => onChange({ status: "verified" })}><Check size={16} />通过</button>
        <button className={field.status === "rejected" ? "active fail" : ""} onClick={() => onChange({ status: "rejected" })}><X size={16} />不通过</button>
        <button className={field.status === "not_applicable" ? "active neutral" : ""} onClick={() => onChange({ status: "not_applicable" })}><CircleHelp size={16} />不适用</button>
        {field.status !== "pending" && <button className="icon-button reset" title="重置为待审核" onClick={() => onChange({ status: "pending", corrected_value: null, comment: "" })}><RotateCcw size={15} /></button>}
      </div>
      {rejectMode && (
        <div className="correction-panel">
          <label>问题说明 <span>必填</span><textarea value={field.comment} onChange={(event) => onChange({ comment: event.target.value })} placeholder="说明原字段的问题及判断依据" /></label>
          {item.kind === "image" && candidatePaths.length > 0 ? (
            <label>正确图片相对路径<select value={typeof field.corrected_value === "string" ? field.corrected_value : ""} onChange={(event) => onChange({ corrected_value: event.target.value })}>
              <option value="">选择候选图片</option>
              {candidatePaths.map((path) => <option key={path} value={path}>{path}</option>)}
            </select></label>
          ) : (
            <label>人工修正值<textarea value={typeof field.corrected_value === "string" ? field.corrected_value : field.corrected_value ? JSON.stringify(field.corrected_value, null, 2) : ""} onChange={(event) => onChange({ corrected_value: event.target.value })} placeholder="填写正确内容" /></label>
          )}
          <button className="confirm-correction" disabled={!field.comment.trim() || field.corrected_value === null || field.corrected_value === ""} onClick={onConfirmCorrection}><Check size={16} />确认修正</button>
        </div>
      )}
    </div>
  );
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function NoteEditor({ field, onChange }: {
  field: FieldReview;
  onChange: (patch: Partial<FieldReview>) => void;
}) {
  const images = field.note_images ?? [];

  const addImages = async (files: FileList | File[] | null) => {
    const list = Array.from(files ?? []).filter((file) => file.type.startsWith("image/"));
    if (!list.length) return;
    const added = await Promise.all(list.map(async (file) => ({
      name: file.name || "pasted-image",
      mime: file.type,
      data_url: await readFileAsDataUrl(file),
      added_at: new Date().toISOString(),
    })));
    onChange({ note_images: [...images, ...added] });
  };

  const removeImage = (index: number) => {
    onChange({ note_images: images.filter((_, position) => position !== index) });
  };

  return (
    <div className="note-editor">
      <label className="note-label">标注说明<span>文字 / 图片，选填</span></label>
      <textarea
        className="note-text"
        value={field.note ?? ""}
        placeholder="补充说明、判断理由或备注。可直接 Ctrl+V 粘贴截图。"
        onChange={(event) => onChange({ note: event.target.value })}
        onPaste={(event) => {
          const files = Array.from(event.clipboardData?.items ?? [])
            .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
            .map((item) => item.getAsFile())
            .filter((file): file is File => Boolean(file));
          if (files.length) {
            event.preventDefault();
            void addImages(files);
          }
        }}
      />
      <div className="note-actions">
        <label className="note-upload">
          <ImageIcon size={14} />添加图片
          <input
            type="file"
            accept="image/*"
            multiple
            onChange={(event) => { void addImages(event.target.files); event.target.value = ""; }}
          />
        </label>
      </div>
      {images.length > 0 && (
        <div className="note-thumbs">
          {images.map((image, index) => (
            <div className="note-thumb" key={`${image.added_at}-${index}`}>
              <img src={image.data_url} alt={image.name} loading="lazy" />
              <button type="button" className="note-thumb-remove" title="删除图片" onClick={() => removeImage(index)}><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FieldCard({
  patentCase,
  item,
  field,
  onFieldChange,
  onConfirmCorrection,
  onOpenResource,
}: {
  patentCase: PatentCase;
  item: ReviewItem;
  field: FieldReview;
  onFieldChange: (patch: Partial<FieldReview>) => void;
  onConfirmCorrection: () => void;
  onOpenResource: (resource: ViewerResource) => void;
}) {
  const imagePath = item.kind === "image" && isRecord(item.originalValue) && typeof item.originalValue.image_path === "string"
    ? item.originalValue.image_path
    : "";
  return (
    <article className={`field-card field-${field.status}`} id={`field-${encodeURIComponent(item.id)}`}>
      <div className="field-heading">
        <div>
          <span className="field-path">{item.id}</span>
          <h3>{item.label}</h3>
        </div>
        <span className={`field-state state-${field.status}`}>{FIELD_STATUS_LABELS[field.status]}</span>
      </div>
      {item.missingRequired && <div className="blocking-note"><AlertTriangle size={17} />必需字段缺失，请标记不通过并填写修正值。</div>}
      {item.missingResources?.map((path) => <div className="blocking-note" key={path}><AlertTriangle size={17} />资源不存在：{path}</div>)}
      {imagePath ? <ResourceImage patentCase={patentCase} path={imagePath} onOpen={onOpenResource} /> : compactRecord(item.originalValue)}
      {item.evidencePaths.length > 0 && (
        <div className="evidence-links">
          {item.evidencePaths.map((path) => <button key={path} onClick={() => onOpenResource({ path, title: item.label })}><FileSearch size={14} />{path}</button>)}
        </div>
      )}
      {field.status === "corrected_verified" && <div className="corrected-value"><strong>人工修正</strong>{displayValue(field.corrected_value ?? undefined)}<small>{field.comment}</small></div>}
      <ReviewControls item={item} field={field} candidatePaths={patentCase.candidateImagePaths} onChange={onFieldChange} onConfirmCorrection={onConfirmCorrection} />
      <NoteEditor field={field} onChange={onFieldChange} />
    </article>
  );
}

export default function App() {
  const [cases, setCases] = useState<PatentCase[]>([]);
  const [reviews, setReviews] = useState<Record<string, ReviewDocument>>({});
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<OverallStatus | "all">("all");
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");
  const [savedHandle, setSavedHandle] = useState<FileSystemDirectoryHandle>();
  const [viewer, setViewer] = useState<ViewerState>();
  const folderInputRef = useRef<HTMLInputElement>(null);

  const applyImported = useCallback(async (imported: PatentCase[], handle?: FileSystemDirectoryHandle) => {
    if (!imported.length) throw new Error("没有找到 *-analysis.json，请选择 case 或其上级目录。");
    const loadedReviews: Record<string, ReviewDocument> = {};
    const finalCases: PatentCase[] = [];
    for (const patentCase of imported) {
      const stored = await loadReview(patentCase.applicationNumber);
      const changed = Boolean(stored && !fingerprintMatches(stored.source_fingerprint, patentCase.fingerprint));
      loadedReviews[patentCase.id] = stored && !changed
        ? updateReviewSummary(stored, patentCase.reviewItems)
        : createReview(patentCase.applicationNumber, patentCase.fingerprint, patentCase.reviewItems);
      finalCases.push({ ...patentCase, sourceChanged: changed });
    }
    setCases(finalCases);
    setReviews(loadedReviews);
    setSelectedId((current) => current && finalCases.some((item) => item.id === current) ? current : finalCases[0].id);
    if (handle) {
      setSavedHandle(handle);
      await saveRootHandle(handle).catch(() => undefined);
    }
  }, []);

  const loadDirectory = useCallback(async (handle: FileSystemDirectoryHandle) => {
    setError("");
    setLoading("正在扫描分析结果...");
    try {
      const imported = await importCases(handle, setLoading);
      await applyImported(imported, handle);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading("");
    }
  }, [applyImported]);

  const loadFiles = useCallback(async (fileList: FileList | File[]) => {
    setError("");
    setLoading("正在读取文件夹...");
    try {
      const imported = await importCasesFromFiles(fileList, setLoading);
      await applyImported(imported);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading("");
    }
  }, [applyImported]);

  useEffect(() => {
    if (!window.showDirectoryPicker) return;
    loadRootHandle().then(async (handle) => {
      if (!handle) return;
      setSavedHandle(handle);
      if (await handle.queryPermission({ mode: "read" }) === "granted") await loadDirectory(handle);
    }).catch(() => undefined);
  }, [loadDirectory]);

  useEffect(() => () => { if (viewer?.url) URL.revokeObjectURL(viewer.url); }, [viewer]);

  const chooseDirectory = async () => {
    if (!window.showDirectoryPicker) {
      folderInputRef.current?.click();
      return;
    }
    try {
      const handle = await window.showDirectoryPicker({ mode: "read" });
      await loadDirectory(handle);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const reconnectDirectory = async () => {
    if (!window.showDirectoryPicker || !savedHandle) return chooseDirectory();
    const permission = await savedHandle.requestPermission({ mode: "read" });
    if (permission === "granted") await loadDirectory(savedHandle);
  };

  const selectedCase = cases.find((patentCase) => patentCase.id === selectedId);
  const selectedReview = selectedCase ? reviews[selectedCase.id] : undefined;

  const filteredCases = useMemo(() => cases.filter((patentCase) => {
    const status = caseOverallStatus(patentCase, reviews[patentCase.id]);
    const text = `${patentCase.applicationNumber} ${patentCase.category}`.toLowerCase();
    return (filter === "all" || status === filter) && text.includes(query.trim().toLowerCase());
  }), [cases, filter, query, reviews]);

  const sections = useMemo(() => {
    const grouped = new Map<string, ReviewItem[]>();
    selectedCase?.reviewItems.forEach((reviewItem) => grouped.set(reviewItem.section, [...(grouped.get(reviewItem.section) || []), reviewItem]));
    return [...grouped.entries()];
  }, [selectedCase]);

  const changeField = async (item: ReviewItem, patch: Partial<FieldReview>) => {
    if (!selectedCase || !selectedReview) return;
    const previous = selectedReview.fields[item.id];
    const updatedField: FieldReview = {
      ...previous,
      ...patch,
      updated_at: new Date().toISOString(),
    };
    const updated = updateReviewSummary({
      ...selectedReview,
      fields: { ...selectedReview.fields, [item.id]: updatedField },
    }, selectedCase.reviewItems);
    setReviews((current) => ({ ...current, [selectedCase.id]: updated }));
    await saveReview(updated);
  };

  const confirmCorrection = async (item: ReviewItem) => {
    if (!selectedCase || !selectedReview) return;
    const field = selectedReview.fields[item.id];
    if (!field.comment.trim() || field.corrected_value === null || field.corrected_value === "") return;
    if (item.kind === "image" && typeof field.corrected_value === "string") {
      if (!await getResourceFile(selectedCase, field.corrected_value)) {
        setError(`修正图片不存在：${field.corrected_value}`);
        return;
      }
    }
    await changeField(item, { status: "corrected_verified" });
  };

  const openResource = async (resource: ViewerResource) => {
    if (!selectedCase) return;
    if (viewer?.url) URL.revokeObjectURL(viewer.url);
    setViewer({ ...resource });
    const file = await getResourceFile(selectedCase, resource.path);
    if (!file) {
      setViewer({ ...resource, error: `找不到文件：${resource.path}` });
      return;
    }
    const mime = file.type || (file.name.endsWith(".pdf") ? "application/pdf" : file.name.endsWith(".html") ? "text/html" : "text/plain");
    if (mime === "text/html") {
      const html = await inlineHtmlImages(selectedCase, await file.text());
      setViewer({ ...resource, mime, srcdoc: html });
    } else if (mime.startsWith("text/")) {
      setViewer({ ...resource, mime, text: await file.text() });
    } else {
      setViewer({ ...resource, mime, url: URL.createObjectURL(file) });
    }
  };

  const nextPending = () => {
    if (!selectedCase || !selectedReview) return;
    const next = selectedCase.reviewItems.find((item) => {
      const status = selectedReview.fields[item.id]?.status;
      return status === "pending" || status === "rejected" || !status;
    });
    if (next) document.getElementById(`field-${encodeURIComponent(next.id)}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const exportAll = () => {
    const documents = cases.map((patentCase) => reviews[patentCase.id]).filter(Boolean);
    downloadJson("patent-review-summary.json", {
      schema_version: "1.0",
      exported_at: new Date().toISOString(),
      total: documents.length,
      status_counts: {
        success: documents.filter((document) => document.overall_status === "success").length,
        needs_correction: documents.filter((document) => document.overall_status === "needs_correction").length,
        pending: documents.filter((document) => document.overall_status === "pending").length,
      },
      reviews: documents,
    });
  };

  return (
    <div className="app-shell">
      <input
        ref={folderInputRef}
        type="file"
        {...({ webkitdirectory: "" } as any)}
        multiple
        style={{ display: "none" }}
        onChange={(event) => {
          const files = event.target.files;
          if (files && files.length) void loadFiles(files);
          event.target.value = "";
        }}
      />
      <header className="topbar">
        <div className="brand"><div className="brand-mark">EP</div><div><strong>专利审查工作台</strong><span>本地分析结果逐字段核验</span></div></div>
        <div className="top-actions">
          {cases.length > 0 && <button onClick={exportAll}><Download size={17} />导出全部</button>}
          <button className="primary" onClick={chooseDirectory}><FolderOpen size={17} />导入文件夹</button>
        </div>
      </header>

      {error && <div className="global-message error-message"><AlertTriangle size={18} />{error}<button onClick={() => setError("")}><X size={16} /></button></div>}
      {loading && <div className="global-message loading-message"><span className="spinner" />{loading}</div>}

      {cases.length === 0 && !loading ? (
        <main className="empty-state">
          <div className="empty-icon"><FileJson size={42} /></div>
          <h1>导入已经分析好的专利</h1>
          <p>选择单个 case 文件夹，或选择包含多个疾病分类和 case 的根目录。文件只在本机读取，不会上传。</p>
          <button className="primary large" onClick={window.showDirectoryPicker && savedHandle ? reconnectDirectory : chooseDirectory}><FolderOpen size={19} />{window.showDirectoryPicker && savedHandle ? "重新授权上次目录" : "选择专利数据文件夹"}</button>
          <div className="expected-files"><span>*-analysis.json</span><span>*-benchmark-input.json</span><span>*-claims-verified.json</span><span>docs / assets / PDF</span></div>
        </main>
      ) : (
        <div className={`workspace ${viewer ? "with-viewer" : ""}`}>
          <aside className="case-sidebar">
            <div className="sidebar-tools">
              <label className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索申请号或分类" /></label>
              <div className="filter-tabs">
                {(["all", "pending", "needs_correction", "success"] as const).map((status) => <button key={status} className={filter === status ? "active" : ""} onClick={() => setFilter(status)}>{status === "all" ? "全部" : STATUS_LABELS[status]}</button>)}
              </div>
            </div>
            <div className="case-count">{filteredCases.length} / {cases.length} 件专利</div>
            <nav className="case-list">
              {filteredCases.map((patentCase) => {
                const review = reviews[patentCase.id];
                const status = caseOverallStatus(patentCase, review);
                const percent = review?.progress.total ? Math.round(review.progress.completed / review.progress.total * 100) : 0;
                return <button key={patentCase.id} className={selectedId === patentCase.id ? "active" : ""} onClick={() => setSelectedId(patentCase.id)}>
                  <div className="case-row"><strong>{patentCase.applicationNumber}</strong><span className={overallClass(status)}>{STATUS_LABELS[status]}</span></div>
                  <div className="case-meta"><span>{patentCase.category}</span><span>{percent}%</span></div>
                  <div className="mini-progress"><i style={{ width: `${percent}%` }} /></div>
                </button>;
              })}
            </nav>
          </aside>

          {selectedCase && selectedReview && (
            <main className="review-main">
              <div className="review-summary">
                <div>
                  <span className="eyebrow">{selectedCase.category}</span>
                  <h1>{selectedCase.applicationNumber}</h1>
                  <p>{selectedCase.relativePath}</p>
                </div>
                <div className="summary-actions">
                  {selectedCase.htmlName && <button onClick={() => openResource({ path: selectedCase.htmlName!, title: "原始分析报告" })}><FileSearch size={16} />原报告</button>}
                  <button onClick={() => downloadJson(`${selectedCase.applicationNumber}-review.json`, selectedReview)}><Download size={16} />导出审核</button>
                  <button className="primary" onClick={nextPending}>下一个未审核<ChevronRight size={16} /></button>
                </div>
                <div className="progress-panel">
                  <div className="progress-number"><strong>{selectedReview.progress.completed}</strong><span>/ {selectedReview.progress.total}</span></div>
                  <div className="progress-copy"><span className={overallClass(selectedReview.overall_status)}>{STATUS_LABELS[selectedReview.overall_status]}</span><small>{selectedReview.progress.total ? Math.round(selectedReview.progress.completed / selectedReview.progress.total * 100) : 0}% 已完成</small></div>
                </div>
              </div>
              {selectedCase.sourceChanged && <div className="source-warning"><AlertTriangle size={18} /><div><strong>源分析文件已经变化</strong><p>旧审核结果未沿用，本次需要重新校验。</p></div></div>}
              <nav className="section-nav">{sections.map(([section]) => <a key={section} href={`#section-${encodeURIComponent(section)}`}>{section}</a>)}</nav>
              <div className="review-sections">
                {sections.map(([section, items]) => <section key={section} id={`section-${encodeURIComponent(section)}`}>
                  <div className="section-heading"><h2>{section}</h2><span>{items.filter((item) => ["verified", "corrected_verified", "not_applicable"].includes(selectedReview.fields[item.id]?.status)).length} / {items.length}</span></div>
                  <div className="field-list">{items.map((item) => <FieldCard key={item.id} patentCase={selectedCase} item={item} field={selectedReview.fields[item.id]} onFieldChange={(patch) => changeField(item, patch)} onConfirmCorrection={() => confirmCorrection(item)} onOpenResource={openResource} />)}</div>
                </section>)}
              </div>
            </main>
          )}
          <EvidenceViewer viewer={viewer} onClose={() => setViewer(undefined)} />
        </div>
      )}
    </div>
  );
}
