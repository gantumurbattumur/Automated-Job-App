from functools import lru_cache

import pdfplumber

from .config import ROOT, load


@lru_cache
def lane_texts() -> dict[str, str]:
    cfg = load("resumes")
    out = {}
    for lane, path in cfg["lanes"].items():
        with pdfplumber.open(ROOT / path) as pdf:
            out[lane] = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return out
