from __future__ import annotations

import runpy
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TARGET = _HERE / "processing" / "ocr_case_batch.py"

for _path in [_HERE, _TARGET.parent, *[p for p in _HERE.iterdir() if p.is_dir()]]:
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

if __name__ == "__main__":
    runpy.run_path(str(_TARGET), run_name="__main__")
else:
    _namespace = runpy.run_path(str(_TARGET), run_name=f"_wrapped_{Path(__file__).stem.replace('-', '_')}")
    globals().update({key: value for key, value in _namespace.items() if not key.startswith("__")})
