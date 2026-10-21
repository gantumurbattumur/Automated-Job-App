import re

import httpx

from ..models import Job
from .common import is_intern, iso
from .greenhouse import _text

API = "https://api.smartrecruiters.com/v1/companies/{co}/postings"
URL = re.compile(r"https://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)/(\d{6,})")


def _loc(l: dict) -> str:
    parts = [l.get("city"), l.get("region"), (l.get("country") or "").upper()]
    return ", ".join(p for p in parts if p) + (" (Remote)" if l.get("remote") else "")


def parse(co: str, postings: list[dict], company: str | None = None) -> list[Job]:
    return [Job(
        id=f"smartrecruiters:{co}:{p['id']}", source="smartrecruiters", company=company or (p.get("company") or {}).get("name", co),
        title=p["name"], location=_loc(p.get("location") or {}), url=f"https://jobs.smartrecruiters.com/{co}/{p['id']}",
        posted_at=iso(p.get("releasedDate")), meta={"board": co},
    ) for p in postings if is_intern(p.get("name", ""))]


def fetch(co: str, client: httpx.Client, company: str | None = None) -> list[Job]:
    r = client.get(API.format(co=co), params={"q": "intern", "limit": 100}, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return parse(co, r.json().get("content", []), company)


def detail(url: str, client: httpx.Client) -> dict | None:
    m = URL.match(url)
    if not m:
        return None
    r = client.get(f"{API.format(co=m[1])}/{m[2]}", timeout=30)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    sections = ((r.json().get("jobAd") or {}).get("sections") or {}).values()
    return {"description": " ".join(_text(s.get("text", "")) for s in sections if isinstance(s, dict))}
