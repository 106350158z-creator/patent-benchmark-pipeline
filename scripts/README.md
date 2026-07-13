# Scripts Layout

The original top-level script names are kept as compatibility wrappers. The actual implementations are grouped by purpose:

- `processing/`: OCR, text extraction, benchmark input generation, Markush rendering, LLM analysis, and report generation.
- `collection/`: Google Patents candidate collection, EPO Publication Server fetching, ledger updates, and artifact classification.
- `legacy_register_pipeline/`: older EPO Register GUI pipeline. Keep for reference or small manual runs; bulk fetching through Register was blocked by EPO challenge/RobotAbuse during this project.
- `audits/`: case quality, completeness, prior-art, and raw-material audit tools.
- `maintenance/`: one-off migration or schema upgrade utilities.

`requirements-ocr.txt` remains at the top level so existing install instructions still work.

For large raw-material collection, use the compatibility entry `scripts/run_target_benchmark_raw_materials.py`. Its default path is now raw fetch only and counts success only when the complete source set exists: Register main/doclist files, file-wrapper docs PDF, and original-application PDF. During fetch-only collection, missing original-application PDFs are filled from the EPO Publication Server by default. Local OCR/text extraction/rendering should run in later processing or remote AutoDL stages.
