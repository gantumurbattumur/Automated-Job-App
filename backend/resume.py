"""Your resumes ("lanes"): which files, which is primary, version history, and text extraction.

Stored in data/resumes.yaml + data/resume/<lane>/ (personal, gitignored). Replacing a file re-ranks every job right away:
the embedding of the resume is recomputed and the feed cache is invalidated — no restart needed.
"""
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

import yaml

from .config import DATA, ROOT

CONF = DATA / "resumes.yaml"
STORE = DATA / "resume"
ALLOWED = {".pdf", ".docx", ".txt", ".md"}
MAX_BYTES = 5 * 1024 * 1024
MIN_CHARS = 200  # less than this usually means a scanned (image-only) PDF
_lock = threading.RLock()
_text_cache: dict[str, tuple[float, str]] = {}  # path -> (mtime, text)

DEFAULT_TARGETS = {
    "ai_ml": ["machine learning", "ml", "ai", "artificial intelligence", "llm", "nlp", "data science", "data scientist",
              "applied scientist", "research", "genai", "deep learning", "computer vision", "data"],
    "swe": ["software", "backend", "full stack", "fullstack", "platform", "infrastructure", "developer", "engineer"],
}


# --- config ---------------------------------------------------------------

def _migrate_old() -> dict:
    """First run: move the old config/resumes.yaml (paths only) into data/resumes.yaml."""
    old_path = ROOT / "config" / "resumes.yaml"
    old = yaml.safe_load(old_path.read_text()) if old_path.exists() else {}
    lanes = {}
    for name, p in (old.get("lanes") or {}).items():
        path = Path(p) if isinstance(p, str) else Path(p.get("file", ""))
        path = path if path.is_absolute() else ROOT / path
        try:
            rel = str(path.relative_to(DATA))
        except ValueError:
            rel = str(path)
        lanes[name] = {"file": rel, "label": name.replace("_", "/").upper(),
                       "targets": (old.get("targets") or {}).get(name) or DEFAULT_TARGETS.get(name, []), "uploaded_at": None}
    return {"primary": old.get("primary") or next(iter(lanes), ""), "lanes": lanes}


def config() -> dict:
    with _lock:
        if not CONF.exists():
            _write(_migrate_old())
        return yaml.safe_load(CONF.read_text()) or {"primary": "", "lanes": {}}


def _write(cfg: dict) -> None:
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))


def primary() -> str:
    return config()["primary"]


def names() -> list[str]:
    return list(config()["lanes"])


def path(lane: str) -> Path:
    f = Path(config()["lanes"][lane]["file"])
    return f if f.is_absolute() else DATA / f


def targets() -> dict[str, list[str]]:
    return {n: l.get("targets") or [] for n, l in config()["lanes"].items()}


# --- text -----------------------------------------------------------------

def extract(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(p) as pdf:
            return "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    if ext == ".docx":
        import docx
        d = docx.Document(str(p))
        return "\n".join(par.text for par in d.paragraphs)
    return p.read_text(errors="replace")


def text(lane: str) -> str:
    p = path(lane)
    mtime = p.stat().st_mtime
    hit = _text_cache.get(str(p))
    if not hit or hit[0] != mtime:
        hit = _text_cache[str(p)] = (mtime, extract(p))
    return hit[1]


def lane_texts() -> dict[str, str]:
    return {n: text(n) for n in names()}


def pages(p: Path) -> int | None:
    if p.suffix.lower() != ".pdf":
        return None
    import pdfplumber
    with pdfplumber.open(p) as pdf:
        return len(pdf.pages)


# --- changes ----------------------------------------------------------------

def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30]
    if not s:
        raise ValueError("Give the resume a name, e.g. “Data analyst”")
    return s


def _save_file(lane_dir: Path, filename: str, data: bytes) -> Path:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"Use a PDF, DOCX or TXT file (got {ext or 'no extension'})")
    if len(data) > MAX_BYTES:
        raise ValueError("That file is over 5 MB")
    lane_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", Path(filename).stem)[:60] or "resume"
    dest = lane_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{safe}{ext}"
    dest.write_bytes(data)
    try:
        chars = len(extract(dest).strip())
    except Exception as e:
        dest.unlink()
        raise ValueError(f"Couldn't read that file: {e}")
    if chars < MIN_CHARS:
        dest.unlink()
        raise ValueError("Almost no text could be read — is it a scanned image? Export a text-based PDF from Word/Docs.")
    return dest


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(DATA))
    except ValueError:
        return str(p)


def add(label: str, filename: str, data: bytes, targets_: list[str] | None = None) -> str:
    with _lock:
        cfg = config()
        name = slug(label)
        if name in cfg["lanes"]:
            raise ValueError(f"You already have a resume called “{label}” — replace its file instead")
        dest = _save_file(STORE / name, filename, data)
        cfg["lanes"][name] = {"file": _rel(dest), "label": label.strip(), "targets": targets_ or [],
                              "uploaded_at": datetime.now().isoformat(timespec="seconds")}
        cfg["primary"] = cfg["primary"] or name
        _write(cfg)
    changed()
    return name


def replace(lane: str, filename: str, data: bytes) -> None:
    with _lock:
        cfg = config()
        if lane not in cfg["lanes"]:
            raise KeyError(lane)
        dest = _save_file(path(lane).parent, filename, data)  # older files stay next to it as versions
        cfg["lanes"][lane].update(file=_rel(dest), uploaded_at=datetime.now().isoformat(timespec="seconds"))
        _write(cfg)
    changed()


def update(lane: str, label: str | None = None, targets_: list[str] | None = None, make_primary: bool = False) -> None:
    with _lock:
        cfg = config()
        if lane not in cfg["lanes"]:
            raise KeyError(lane)
        if label is not None and label.strip():
            cfg["lanes"][lane]["label"] = label.strip()
        if targets_ is not None:
            cfg["lanes"][lane]["targets"] = [t.strip().lower() for t in targets_ if t.strip()]
        if make_primary:
            cfg["primary"] = lane
        _write(cfg)
    changed()


def versions(lane: str) -> list[dict]:
    cur = path(lane)
    files = [f for f in cur.parent.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED and f != cur] if cur.parent.exists() else []
    return [{"file": f.name, "saved_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
            for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)]


def revert(lane: str, version_file: str) -> None:
    with _lock:
        cfg = config()
        target = path(lane).parent / Path(version_file).name  # name only: no path tricks
        if not target.exists() or target.suffix.lower() not in ALLOWED:
            raise KeyError(version_file)
        cfg["lanes"][lane].update(file=_rel(target), uploaded_at=datetime.now().isoformat(timespec="seconds"))
        _write(cfg)
    changed()


def delete(lane: str) -> None:
    with _lock:
        cfg = config()
        if lane not in cfg["lanes"]:
            raise KeyError(lane)
        if len(cfg["lanes"]) == 1:
            raise ValueError("Keep at least one resume")
        src = path(lane).parent
        del cfg["lanes"][lane]
        if cfg["primary"] == lane:
            cfg["primary"] = next(iter(cfg["lanes"]))
        _write(cfg)
        if src.exists() and src.parent == STORE:  # keep the files, just out of the way
            shutil.move(str(src), str(STORE / "_removed" / f"{lane}-{datetime.now():%Y%m%d-%H%M%S}"))
    changed()


def changed() -> None:
    """A resume changed: drop everything derived from the old text so the next ranking uses the new one."""
    from . import feed, relevance
    relevance.lanes.cache_clear()
    feed.invalidate()
