import argparse
from pathlib import Path

import fitz


def extract_pdf_text(pdf_path: Path, overwrite: bool) -> Path:
    out_path = pdf_path.with_suffix(".txt")
    if out_path.exists() and not overwrite:
        return out_path

    doc = fitz.open(pdf_path)
    chunks: list[str] = []
    for index, page in enumerate(doc, start=1):
        text = page.get_text("text")
        chunks.append(f"\n--- PAGE {index} ---\n")
        chunks.append(text or "")
        if text and not text.endswith("\n"):
            chunks.append("\n")
    out_path.write_text("".join(chunks), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract embedded text from PDFs into sibling .txt files.")
    parser.add_argument("input", help="PDF file or directory containing PDFs")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    pdfs = [input_path] if input_path.is_file() else sorted(input_path.rglob("*.pdf"))
    for pdf in pdfs:
        out = extract_pdf_text(pdf, args.overwrite)
        print(f"{pdf} -> {out}")


if __name__ == "__main__":
    main()
