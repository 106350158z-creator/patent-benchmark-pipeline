import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


PAGE_RE = re.compile(r"---\s*PAGE\s+(\d+)\s*---", re.I)
KEYWORDS = [
    "markush",
    "formula",
    "compound represented by the following formula",
    "scope of formula",
    "wherein",
]


@dataclass(frozen=True)
class Candidate:
    page: int
    bbox: tuple[int, int, int, int]
    score: float
    line_count: int
    slanted_line_count: int
    density: float
    path: Path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def text_pages(text: str) -> list[tuple[int, str]]:
    matches = list(PAGE_RE.finditer(text))
    if not matches:
        return [(1, text)]
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((int(match.group(1)), text[start:end]))
    return pages


def source_to_pdf(docs_dir: Path, source: str) -> Path | None:
    stem = re.sub(r"_(ocr|text)\.txt$", ".pdf", source, flags=re.I)
    pdf = docs_dir / stem
    return pdf if pdf.exists() else None


def score_page(page_text: str) -> int:
    lower = page_text.lower()
    score = 0
    for keyword in KEYWORDS:
        score += lower.count(keyword) * 3
    score += len(re.findall(r"\bR\d*[A-Z]?\b|\bX\d*[A-Z]?\b|\bFormula\s*\(?[IVX0-9]+\)?", page_text, re.I))
    return score


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def overlap_score(needle: str, haystack: str) -> int:
    needle_tokens = [token for token in normalize_for_match(needle).split() if len(token) > 2]
    if not needle_tokens:
        return 0
    haystack_norm = " " + normalize_for_match(haystack) + " "
    seen = set()
    score = 0
    for token in needle_tokens[:80]:
        if token in seen:
            continue
        seen.add(token)
        if f" {token} " in haystack_norm:
            score += 1
    return score


def locate_snippet_pages(ocr_path: Path, snippet_text: str, max_pages: int) -> list[int]:
    if not snippet_text:
        return []
    text = ocr_path.read_text(encoding="utf-8-sig", errors="ignore")
    pages = text_pages(text)
    snippet_norm = normalize_for_match(snippet_text)
    formula_ids = re.findall(r"formula\s*\(?\s*([ivx0-9]+)\s*\)?", snippet_text, re.I)
    if formula_ids:
        anchored: list[tuple[int, int]] = []
        wants_scope = "scope of formula" in snippet_text.lower()
        for page_number, page_text in pages:
            page_norm = normalize_for_match(page_text)
            has_formula = any(f"formula {formula_id.lower()}" in page_norm for formula_id in formula_ids)
            if not has_formula:
                continue
            if wants_scope and "scope" not in page_norm:
                continue
            anchored.append((overlap_score(snippet_text, page_text), page_number))
        anchored = [(score, page_number) for score, page_number in anchored if score >= 5]
        if anchored:
            anchored.sort(key=lambda item: (-item[0], item[1]))
            return [page_number for _score, page_number in anchored[:max_pages]]

    if "chemical formulae" in snippet_norm or "definitions" in snippet_norm:
        return []

    scored = [(overlap_score(snippet_text, page_text), page_number) for page_number, page_text in pages]
    scored = [(score, page_number) for score, page_number in scored if score >= 18]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [page_number for _score, page_number in scored[:max_pages]]


def snippet_priority(text: str) -> int:
    lower = text.lower()
    priority = 0
    if "markush" in lower:
        priority += 1800
    if "scope of formula" in lower or "represented by the following formula" in lower:
        priority += 1600
    if re.search(r"formula\s*\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|\d+)\)", lower):
        priority += 1300
    if "wherein" in lower and "formula" in lower:
        priority += 600
    if "claim 1" in lower or "compound of claim" in lower:
        priority += 250
    if "chemical formulae" in lower or "definitions" in lower or "tautomers" in lower:
        priority -= 900
    return priority


def select_pages(ocr_path: Path, max_pages: int) -> list[int]:
    text = ocr_path.read_text(encoding="utf-8-sig", errors="ignore")
    scored = [(score_page(page_text), page_number) for page_number, page_text in text_pages(text)]
    scored = [(score, page_number) for score, page_number in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1]))
    pages: list[int] = []
    for _, page_number in scored:
        if page_number not in pages:
            pages.append(page_number)
        if len(pages) >= max_pages:
            break
    return pages


def render_page_array(page: fitz.Page, zoom: float) -> np.ndarray:
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
    if pix.n == 4:
        image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
    return image


def save_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


def binarize(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return binary


def merge_boxes(boxes: list[tuple[int, int, int, int]], pad: int = 8) -> list[tuple[int, int, int, int]]:
    merged: list[tuple[int, int, int, int]] = []
    for box in boxes:
        x, y, w, h = box
        rect = (x - pad, y - pad, x + w + pad, y + h + pad)
        changed = True
        while changed:
            changed = False
            next_merged = []
            for other in merged:
                if intersects(rect, other):
                    rect = (
                        min(rect[0], other[0]),
                        min(rect[1], other[1]),
                        max(rect[2], other[2]),
                        max(rect[3], other[3]),
                    )
                    changed = True
                else:
                    next_merged.append(other)
            merged = next_merged
        merged.append(rect)
    return [(x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in merged]


def intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def candidate_boxes(binary: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = binary.shape
    boxes: list[tuple[int, int, int, int]] = []

    component_boxes: list[tuple[int, int, int, int, int]] = []
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    for index in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[index]]
        if area < 8:
            continue
        if y < height * 0.035 or y + h > height * 0.965:
            continue
        component_boxes.append((x, y, w, h, area))

    seeds: list[tuple[int, int, int, int]] = []
    for x, y, w, h, area in component_boxes:
        if w > width * 0.72 and h < height * 0.08:
            continue
        is_graphic_seed = (w >= 55 and h >= 35) or (area >= 800 and h >= 25) or (w >= 90 and area >= 300)
        if is_graphic_seed:
            seeds.append((x, y, w, h))

    for seed in seeds:
        sx, sy, sw, sh = seed
        expanded = (sx - 70, sy - 55, sx + sw + 70, sy + sh + 55)
        x0, y0, x1, y1 = sx, sy, sx + sw, sy + sh
        for x, y, w, h, area in component_boxes:
            cx, cy = x + w / 2, y + h / 2
            if expanded[0] <= cx <= expanded[2] and expanded[1] <= cy <= expanded[3]:
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x + w), max(y1, y + h)
        w, h = x1 - x0, y1 - y0
        area_ratio = (w * h) / float(width * height)
        if 0.0015 <= area_ratio <= 0.18 and w >= 45 and h >= 35:
            boxes.append((x0, y0, w, h))

    kernels = [(9, 9), (17, 11), (31, 17), (43, 23)]
    for kx, ky in kernels:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
        dilated = cv2.dilate(binary, kernel, iterations=1)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < 900 or area > width * height * 0.45:
                continue
            if w < 35 or h < 28:
                continue
            if area > width * height * 0.18:
                continue
            if w > width * 0.82 and h > height * 0.18:
                continue
            if w > width * 0.72 and h < height * 0.08:
                continue
            if y < height * 0.035 or y + h > height * 0.965:
                continue
            boxes.append((x, y, w, h))

    deduped: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda item: item[2] * item[3]):
        if any(iou(box, other) > 0.65 for other in deduped):
            continue
        deduped.append(box)
    return deduped


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx1, by1 = bx0 + bw, by0 + bh
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def line_metrics(crop_binary: np.ndarray) -> tuple[int, int]:
    edges = cv2.Canny(crop_binary, 80, 180)
    min_length = max(12, int(min(crop_binary.shape[:2]) * 0.08))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=18, minLineLength=min_length, maxLineGap=4)
    if lines is None:
        return 0, 0
    total = 0
    slanted = 0
    for line in lines[:, 0]:
        x1, y1, x2, y2 = line
        length = math.hypot(x2 - x1, y2 - y1)
        if length < min_length:
            continue
        total += 1
        angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180
        if 12 <= angle <= 78 or 102 <= angle <= 168:
            slanted += 1
    return total, slanted


def score_candidate(crop_binary: np.ndarray, x: int, y: int, w: int, h: int, page_w: int, page_h: int) -> tuple[float, int, int, float]:
    density = float(np.count_nonzero(crop_binary)) / float(max(1, w * h))
    line_count, slanted = line_metrics(crop_binary)
    aspect = w / max(1, h)
    area_ratio = (w * h) / float(page_w * page_h)
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(crop_binary, 8)
    small_components = 0
    large_components = 0
    largest_component_area = 0
    for index in range(1, count):
        cw, ch, area = int(stats[index][2]), int(stats[index][3]), int(stats[index][4])
        if area < 4:
            continue
        largest_component_area = max(largest_component_area, area)
        if cw < 42 and ch < 42:
            small_components += 1
        else:
            large_components += 1

    score = line_count * 1.8 + slanted * 4.0
    if 0.015 <= density <= 0.22:
        score += 10
    if 0.003 <= area_ratio <= 0.16:
        score += 8
    if 0.45 <= aspect <= 4.2:
        score += 5
    if h > page_h * 0.5 or w > page_w * 0.92:
        score -= 18
    if density > 0.32:
        score -= 15
    if aspect > 6 and h < page_h * 0.11 and largest_component_area < 700:
        score -= 1000
    if aspect > 4.8 and largest_component_area < 500:
        score -= 600
    if small_components > 150 and large_components <= 1 and largest_component_area < 700:
        score -= 900
    elif small_components > 90 and large_components <= 1:
        score -= 180
    elif small_components > 180 and large_components <= 2:
        score -= 160
    if large_components >= 1:
        score += min(large_components, 5) * 6
    if largest_component_area > 1200:
        score += 160
    elif largest_component_area > 700:
        score += 90
    if large_components >= 1 and small_components <= 60:
        score += 80
    return score, line_count, slanted, density


def crop_candidates(
    image: np.ndarray,
    binary: np.ndarray,
    page_number: int,
    out_dir: Path,
    stem: str,
    limit: int,
) -> list[Candidate]:
    page_h, page_w = binary.shape
    candidates: list[Candidate] = []
    for index, (x, y, w, h) in enumerate(candidate_boxes(binary), start=1):
        margin = 16
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1, y1 = min(page_w, x + w + margin), min(page_h, y + h + margin)
        crop = image[y0:y1, x0:x1]
        crop_binary = binary[y0:y1, x0:x1]
        score, line_count, slanted, density = score_candidate(crop_binary, x0, y0, x1 - x0, y1 - y0, page_w, page_h)
        if score < 18 or line_count < 2:
            continue
        out_path = out_dir / f"{stem}_p{page_number:03d}_c{index:03d}.png"
        save_rgb(out_path, crop)
        candidates.append(
            Candidate(
                page=page_number,
                bbox=(x0, y0, x1, y1),
                score=round(score, 3),
                line_count=line_count,
                slanted_line_count=slanted,
                density=round(density, 5),
                path=out_path,
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.page, item.bbox[1], item.bbox[0]))
    return candidates[:limit]


def render_page_image(pdf_path: Path, page: fitz.Page, page_number: int, out_dir: Path, image: np.ndarray) -> Path:
    out_path = out_dir / f"{pdf_path.stem}_p{page_number:03d}.png"
    save_rgb(out_path, image)
    return out_path


def resolve_docs_dir(benchmark_path: Path, trace: dict[str, Any]) -> Path:
    docs_dir = Path(trace.get("docs_dir") or benchmark_path.parent / "docs")
    if docs_dir.is_absolute():
        return docs_dir
    if docs_dir.exists():
        return docs_dir.resolve()
    return (benchmark_path.parent / docs_dir).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop and rank likely Markush/Formula images from source PDFs.")
    parser.add_argument("benchmark_input")
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--candidate-limit", type=int, default=36)
    parser.add_argument("--selected-limit", type=int, default=3)
    parser.add_argument("--zoom", type=float, default=2.4)
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    benchmark_path = Path(args.benchmark_input)
    data = load_json(benchmark_path)
    bench_input = data.get("benchmark_input") or {}
    structure = bench_input.get("drug_structure") or {}
    snippets = structure.get("markush_or_formula_snippets") or []
    trace = data.get("source_trace") or {}
    docs_dir = resolve_docs_dir(benchmark_path, trace)

    crop_dir = benchmark_path.parent / "assets" / "markush-candidates"
    page_dir = benchmark_path.parent / "assets" / "markush-pages"
    if args.clear:
        for directory in [crop_dir, page_dir]:
            if directory.exists():
                for item in directory.glob("*.png"):
                    item.unlink()

    candidates: list[dict[str, Any]] = []
    page_images: list[dict[str, Any]] = []
    page_jobs: dict[tuple[Path, int], dict[str, Any]] = {}

    for snippet in snippets:
        if not isinstance(snippet, dict):
            continue
        source = str(snippet.get("source") or "")
        if not source:
            continue
        source_path = docs_dir / source
        pdf_path = source_to_pdf(docs_dir, source)
        if not pdf_path or not source_path.exists():
            continue

        snippet_text = str(snippet.get("text") or "")
        priority = snippet_priority(snippet_text)
        pages = locate_snippet_pages(source_path, snippet_text, min(2, args.max_pages))
        if not pages:
            if priority < 0:
                continue
            pages = select_pages(source_path, args.max_pages)
        for page_number in pages:
            key = (pdf_path, page_number)
            existing = page_jobs.get(key)
            if existing is None or priority > existing["priority"]:
                page_jobs[key] = {"source": source, "priority": priority}

    for (pdf_path, page_number), job in sorted(page_jobs.items(), key=lambda item: (-item[1]["priority"], item[0][0].name, item[0][1])):
        with fitz.open(pdf_path) as doc:
            if page_number < 1 or page_number > doc.page_count:
                continue
            page = doc.load_page(page_number - 1)
            image = render_page_array(page, args.zoom)
            binary = binarize(image)
            page_path = render_page_image(pdf_path, page, page_number, page_dir, image)
            page_images.append(
                {
                    "source": job["source"],
                    "pdf": pdf_path.name,
                    "page": page_number,
                    "image_path": page_path.relative_to(benchmark_path.parent).as_posix(),
                    "context_priority": job["priority"],
                    "extraction_method": "keyword-scored page fallback",
                }
            )
            for candidate in crop_candidates(image, binary, page_number, crop_dir, pdf_path.stem, args.candidate_limit):
                final_score = round(candidate.score + float(job["priority"]), 3)
                candidates.append(
                    {
                        "source": job["source"],
                        "pdf": pdf_path.name,
                        "page": page_number,
                        "image_path": candidate.path.relative_to(benchmark_path.parent).as_posix(),
                        "bbox_px": list(candidate.bbox),
                        "score": final_score,
                        "image_score": candidate.score,
                        "context_priority": job["priority"],
                        "line_count": candidate.line_count,
                        "slanted_line_count": candidate.slanted_line_count,
                        "density": candidate.density,
                        "extraction_method": "OpenCV connected-component crop",
                    }
                )

    deduped: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-float(item["score"]), item["page"], item["image_path"])):
        box = tuple(candidate["bbox_px"])
        page = candidate["pdf"], candidate["page"]
        if any((existing["pdf"], existing["page"]) == page and iou(tuple(existing["bbox_px"]), box) > 0.45 for existing in deduped):
            continue
        deduped.append(candidate)
        if len(deduped) >= args.candidate_limit:
            break

    selected = [item for item in deduped if float(item.get("image_score") or item.get("score") or 0) >= 250]
    if len(selected) < args.selected_limit:
        selected_paths = {item["image_path"] for item in selected}
        selected.extend(item for item in deduped if item["image_path"] not in selected_paths)
    structure["markush_images"] = selected[: args.selected_limit]
    structure["markush_candidate_images"] = deduped
    structure["markush_page_images"] = page_images[: args.max_pages]
    structure["extraction_note"] = (
        "先按 Formula/Markush OCR 上下文定位 PDF 页面，再用 OpenCV 连通域和线段特征切出候选结构图；"
        "markush_images 为自动筛选 Top 候选，markush_candidate_images 保留候选图库供人工复核。"
    )
    bench_input["drug_structure"] = structure
    data["benchmark_input"] = bench_input
    write_json(benchmark_path, data)
    print(f"Cropped {len(deduped)} candidate image(s); selected {len(structure['markush_images'])}.")


if __name__ == "__main__":
    main()
