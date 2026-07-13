from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "legacy_register_pipeline" / "download_epo_docs_browser.py"
    runpy.run_path(str(target), run_name="__main__")
