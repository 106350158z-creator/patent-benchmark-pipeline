# EPO 审查报告爬取与分析流程总结

## 本次使用的数据源

本次只使用公开的 European Patent Register：

- 授权案例：`EP10007106` / 申请号 `10007106.7`，最终状态为 granted，标题 `Model determination system`
- 驳回案例：`EP94912949` / 申请号 `94912949.8`，最终状态为 rejected，标题 `Method of estimating product distribution`

每个案子先访问：

```text
https://register.epo.org/application?number=<EP_NUMBER>&lng=en&tab=main
https://register.epo.org/application?number=<EP_NUMBER>&lng=en&tab=doclist
https://register.epo.org/application?number=<EP_NUMBER>&lng=en&tab=legal
```

`main` 用于确认申请号、标题、申请人、申请日、状态、引用文件和程序历史；`doclist` 用于找具体审查通信、附件、授权/驳回决定的 documentId；`legal` 用于核验最终授权、驳回、异议等法律状态。

## PDF 下载方法

EPO Register 的文档查看页会先打开一个 PDF viewer。关键点是页面内 iframe 的真实 PDF 地址可以直接访问：

```text
https://register.epo.org/application?documentId=<DOCUMENT_ID>&appnumber=<EP_NUMBER>&showPdfPage=all&proc=
```

其中：

- `DOCUMENT_ID` 来自 `doclist` 页每一行 `<a id="...">`
- `EP_NUMBER` 是带 EP 前缀的 Register 号，例如 `EP10007106`
- `showPdfPage=all` 表示下载完整 PDF，而不是第一页

## OCR 方法

这些旧 EPO PDF 多数是扫描图像，普通 PDF 文本提取几乎为空。因此我做了两步：

1. 用 PyMuPDF 把每一页渲染成图片。
2. 用 `rapidocr-onnxruntime` 做本地 OCR，输出为同目录的 `*_ocr.txt`。

整个 OCR 在本机执行，没有上传到第三方 OCR 服务。

## 分析方法

分析时同时使用三类材料：

- Register `main/legal` 页面：提取元数据、状态、引用先文、程序轮次。
- 审查通信和附件 OCR 文本：提取 Art.52、54、56、83、84、123 等审查意见。
- 最终决定或授权意向：确认 `grant_label`、`outcome` 和异议是否最终被克服。

EP 案件的创造性分析按 COMVIK 逻辑处理：

1. 找最接近现有技术。
2. 识别区别特征。
3. 判断区别特征是否贡献技术效果。
4. 非技术业务规则只可作为技术问题的约束，不能支持 Art.56 创造性。

## 本次两个案例的核心结论

### EP10007106 授权案

早期被质疑 Art.52(2)(3) 和 Art.56。审查员认为权利要求中的模型确定、变量、假设、模型评价等主要是业务/抽象规则；技术部分只是通用联网计算机和多维数据存储，EP1492030 已显示 OLAP/多维数据存储背景。但后续文本进入 Rule 71(3) 授权意向，并最终按 Art.97(1) 授权。

### EP94912949 驳回案

最终决定认为主请求和辅助请求均不满足 Art.52(1)，实质上是 Art.52(2)(c) 和 52(3) 排除的商业方法。即使用数据库、处理器、内存等硬件，也只是通用技术手段，没有进一步技术效果。创造性方面，D1 US4972504 作为最接近现有技术，区别特征只是基于距离/空间相关进行销售估计，客观问题属于经济/统计问题；D3+D5 也显示 GIS 距离和空间相关分析背景。

