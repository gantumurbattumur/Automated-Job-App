"""Workday career sites expose an (undocumented but public) JSON API under /wday/cxs/."""
import re

import httpx

from ..models import Job
from .common import is_intern, workday_posted
from .greenhouse import _text

URL = re.compile(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)(/job/.*)?")
PAGES = 3  # 20 results each; titles are filtered locally since Workday's search is fuzzy


def parse_board(board: str) -> tuple[str, str, str]:
    tenant, wd, site = board.split("|")
    return tenant, wd, site


def cxs(tenant: str, wd: str, site: str) -> str:
    return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"


def parse(board: str, postings: list[dict], company: str | None = None) -> list[Job]:
    tenant, wd, site = parse_board(board)
    jobs = []
    for p in postings:
        if not is_intern(p.get("title", "")) or not p.get("externalPath"):
            continue
        loc = p.get("locationsText", "")
        jobs.append(Job(
            id=f"workday:{tenant}:{p['externalPath'].rsplit('_', 1)[-1]}", source="workday", company=company or tenant,
            title=p["title"], location="" if re.fullmatch(r"\d+ Locations", loc) else loc,  # real list comes from enrichment
            url=f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{p['externalPath']}",
            posted_at=workday_posted(p.get("postedOn", "")), meta={"board": board},
        ))
    return jobs


def fetch(board: str, client: httpx.Client, company: str | None = None) -> list[Job]:
    base = cxs(*parse_board(board))
    postings = []
    for page in range(PAGES):
        r = client.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": "intern"},
                        timeout=30)
        if r.status_code in (404, 422):
            return []
        r.raise_for_status()
        d = r.json()
        postings += d.get("jobPostings", [])
        if len(postings) >= d.get("total", 0):
            break
    return parse(board, postings, company)


def detail(url: str, client: httpx.Client) -> dict | None:
    m = URL.match(url)
    if not m or not m[4]:
        return None
    r = client.get(cxs(m[1], m[2], m[3]) + m[4], timeout=30)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    info = r.json().get("jobPostingInfo", {})
    locs = [info.get("location", "")] + info.get("additionalLocations", [])
    country = (info.get("country") or {}).get("descriptor", "")
    return {"description": _text(info.get("jobDescription", "")),
            "location": "; ".join(f"{l}, {country}" if country and country not in l else l for l in locs if l)}
