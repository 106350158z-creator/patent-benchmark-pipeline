# 本地专利审查系统

本应用在浏览器中读取本地专利 case 文件夹，逐项核验分析字段、Markush 图片、证据和权利要求。原始 JSON 与 PDF 始终只读，审核结果保存在浏览器 IndexedDB，并可导出为独立 `review.json`。

## 启动

```powershell
cd F:\benchmark\epo-report-analysis\local-patent-review
npm install
npm run dev
```

打开终端显示的 `http://127.0.0.1:5173`，选择单个 case 目录或包含多个 case 的根目录。Chrome 会请求该目录的只读权限。

## 识别的文件

- `*-analysis.json`：必需，提供审查报告字段。
- `*-benchmark-input.json`：提供申请预览、Markush 图片与相对路径。
- `*-claims-verified.json`：提供逐条权利要求。
- `*-analysis.html`：可在证据查看器中打开原报告。
- `docs/`、`original-application/`、`prior-art/`、`assets/`：按需读取 PDF、文本和图片。

只有所有审核项均为“通过”“修正后通过”，或非阻塞字段明确标为“不适用”时，专利才显示“成功”。缺失的必需字段和资源只能通过填写修正值并再次确认来完成。

## 验证

```powershell
npm test
npm run build
```
