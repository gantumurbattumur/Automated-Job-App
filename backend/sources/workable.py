import re

import httpx

from ..models import Job
from .common import is_intern, iso
from .greenhouse import _text

LIST = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"
DETAIL = "https://apply.workable.com/api/v2/accounts/{slug}/jobs/{code}"
URL = re.compile(r"https://apply\.workable\.com/([\w-]+)/j/([0-9A-F]+)")


def _loc(p: dict) -> str:
    locs = p.get("locations") or [p.get("location") or {}]
    out = ["; ".join(", ".join(x for x in (l.get("city"), l.get("region"), l.get("countryCode")) if x) for l in locs if l)]
    return out[0] + (" (Remote)" if p.get("remote") else "")


def parse(slug: str, postings: list[dict], company: str | None = None) -> list[Job]:
    return [Job(
        id=f"workable:{slug}:{p['shortcode']}", source="workable", company=company or slug, title=p["title"],
        location=_loc(p), url=f"https://apply.workable.com/{slug}/j/{p['shortcode']}/", posted_at=iso(p.get("published")),
        meta={"board": slug},
    ) for p in postings if p.get("state", "published") == "published" and is_intern(p.get("title", ""))]


def fetch(slug: str, client: httpx.Client, company: str | None = None) -> list[Job]:
    r = client.post(LIST.format(slug=slug), json={"query": "intern", "location": [], "department": [], "worktype": [], "remote": []},
                    timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return parse(slug, r.json().get("results", []), company)


def detail(url: str, client: httpx.Client) -> dict | None:
    m = URL.match(url)
    if not m:
        return None
    r = client.get(DETAIL.format(slug=m[1], code=m[2]), timeout=30)
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    d = r.json()
    return {"description": " ".join(_text(d.get(k, "")) for k in ("description", "requirements", "benefits")).strip()}
