import httpx

from ..models import Job
from .common import is_intern, iso

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def board(slug: str, client: httpx.Client) -> list[dict]:
    r = client.get(API.format(slug=slug), timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json().get("jobs", [])


def parse(slug: str, postings: list[dict], company: str | None = None) -> list[Job]:
    jobs = []
    for p in postings:
        if not p.get("isListed", True) or not is_intern(p.get("title", "")):
            continue
        locs = [p.get("location", "")] + [x.get("location", "") for x in p.get("secondaryLocations") or []]
        comp = (p.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
        jobs.append(Job(
            id=f"ashby:{slug}:{p['id']}", source="ashby", company=company or slug, title=p["title"].strip(),
            location="; ".join(x for x in locs if x) + (" (Remote)" if p.get("isRemote") else ""),
            url=p.get("jobUrl", ""), description=f"{p.get('descriptionPlain', '')} {comp}".strip(),
            posted_at=iso(p.get("publishedAt")), meta={"board": slug},
        ))
    return jobs


def fetch(slug: str, client: httpx.Client, company: str | None = None) -> list[Job]:
    return parse(slug, board(slug, client), company)
