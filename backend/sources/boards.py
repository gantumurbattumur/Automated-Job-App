"""Which ATS boards to poll: config/companies.yaml plus boards discovered from SimplifyJobs links."""
import re

from sqlmodel import select

from ..config import load
from ..models import Job, session
from . import workday

PATTERNS = {
    "greenhouse": re.compile(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?!embed)([\w-]+)/jobs/"),
    "lever": re.compile(r"https?://jobs\.lever\.co/([\w.-]+)/"),
    "ashby": re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)/"),
    "smartrecruiters": re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)/"),
    "workable": re.compile(r"https?://apply\.workable\.com/([\w-]+)/j/"),
}


def board_of(source: str, url: str) -> str | None:
    if source == "workday":
        m = workday.URL.match(url)
        return f"{m[1]}|{m[2]}|{m[3]}" if m else None
    m = PATTERNS[source].match(url)
    return m[1] if m else None


def all_boards() -> dict[str, dict[str, str | None]]:
    """{source: {board: company display name or None}}"""
    cfg = load("companies")
    out = {src: {b: None for b in cfg.get(src) or []} for src in (*PATTERNS, "workday")}
    if cfg.get("discover_from_simplify", True):
        with session() as s:
            rows = s.exec(select(Job.url, Job.company).where(Job.source == "simplify")).all()
        for url, company in rows:
            for src in out:
                if (b := board_of(src, url)) and not out[src].get(b):
                    out[src][b] = company
    return out
