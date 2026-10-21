"""H-1B sponsorship history from USCIS's H-1B Employer Data Hub spreadsheets (free, public).

Companies that sponsor H-1B visas usually hire international students, so this is a strong CPT-friendliness signal
when a posting says nothing. The spreadsheets are read once into data/h1b_sponsors.json.
"""
import bisect
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

from .config import DATA, ROOT, load

CACHE = DATA / "h1b_sponsors.json"
APPROVAL_COLUMNS = ("New Employment Approval", "Continuation Approval", "Change with Same Employer Approval",
                    "New Concurrent Approval", "Change of Employer Approval", "Amended Approval")
_lock = threading.Lock()


def norm(name: str) -> str:
    n = re.sub(r"\b(inc|llc|l l c|corp|corporation|co|company|ltd|limited|lp|llp|plc|the|incorporated|na|us|usa)\b", " ",
               name.lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9]", "", n)


def _sources() -> list[Path]:
    cfg = load("h1b") if (ROOT / "config" / "h1b.yaml").exists() else {}
    return [Path(p).expanduser() for p in cfg.get("files", [])]


def build() -> dict[str, int]:
    """{normalized employer: total approvals across the configured fiscal years}"""
    import openpyxl
    totals: dict[str, int] = {}
    for path in _sources():
        if not path.exists():
            continue
        wb = openpyxl.load_workbook(path, read_only=True)
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(rows)]
        name_i = header.index("Employer (Petitioner) Name")
        cols = [header.index(c) for c in APPROVAL_COLUMNS if c in header]
        for r in rows:
            n = norm(str(r[name_i] or ""))
            if n:
                totals[n] = totals.get(n, 0) + sum(int(r[i] or 0) for i in cols)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(totals))
    return totals


@lru_cache
def _index() -> tuple[list[str], dict[str, int]]:
    with _lock:
        stale = not CACHE.exists() or any(p.exists() and p.stat().st_mtime > CACHE.stat().st_mtime for p in _sources())
        data = build() if stale else json.loads(CACHE.read_text())
    return sorted(data), data


@lru_cache(maxsize=20000)
def approvals(company: str) -> int | None:
    """Approvals for this employer (exact name, else the biggest employer whose name starts with it), None if unknown."""
    keys, data = _index()
    if not data:
        return None
    n = norm(company)
    if not n:
        return None
    if n in data:
        return data[n]
    if len(n) < 4:  # "K1", "AI" would match far too much
        return None
    i = bisect.bisect_left(keys, n)
    best = None
    while i < len(keys) and keys[i].startswith(n):
        best = max(best or 0, data[keys[i]])
        i += 1
    return best
