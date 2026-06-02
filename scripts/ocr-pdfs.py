import argparse
from pathlib import Path

import fitz
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


def ocr_pdf(pdf_path: Path, engine: RapidOCR, zoom: float, overwrite: bool) -> Path:
    out_path = pdf_path.with_name(pdf_path.stem + "_ocr.txt")
    if out_path.exists() and not overwrite:
        return out_path

    doc = fitz.open(pdf_path)
    chunks: list[str] = []

    for index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        result, _ = engine(np.array(image))

        chunks.append(f"\n--- PAGE {index} ---\n")
        if result:
            for line in result:
                chunks.append(line[1] + "\n")

    out_path.write_text("".join(chunks), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR image-based EPO PDFs locally.")
    parser.add_argument("input", help="PDF file or directory containing PDFs")
    parser.add_argument("--zoom", type=float, default=2.5, help="Render zoom; higher is slower but clearer")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing *_ocr.txt files")
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.is_file():
        pdfs = [input_path]
    else:
        pdfs = sorted(input_path.rglob("*.pdf"))

    engine = RapidOCR()
    for pdf in pdfs:
        out = ocr_pdf(pdf, engine, args.zoom, args.overwrite)
        print(f"{pdf} -> {out}")


if __name__ == "__main__":
    main()

