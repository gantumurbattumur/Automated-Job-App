import httpx

from ..models import Job
from .common import is_intern, ms
from .greenhouse import _text

API = "https://api.lever.co/v0/postings/{slug}?mode=json"


def description(p: dict) -> str:
    parts = [p.get("descriptionPlain", "")]
    parts += [f"{l.get('text', '')}: {_text(l.get('content', ''))}" for l in p.get("lists", [])]
    parts.append(p.get("additionalPlain", ""))
    return " ".join(x for x in parts if x).strip()


def parse(slug: str, postings: list[dict], company: str | None = None) -> list[Job]:
    jobs = []
    for p in postings:
        if not is_intern(p.get("text", "")):
            continue
        cat = p.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        jobs.append(Job(
            id=f"lever:{slug}:{p['id']}", source="lever", company=company or slug, title=p.get("text", ""),
            location="; ".join(x for x in locs if x) + (" (Remote)" if p.get("workplaceType") == "remote" else ""),
            url=p.get("hostedUrl", ""), description=description(p), posted_at=ms(p.get("createdAt", 0)),
            meta={"board": slug},
        ))
    return jobs


def fetch(slug: str, client: httpx.Client, company: str | None = None) -> list[Job]:
    r = client.get(API.format(slug=slug), timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return parse(slug, r.json(), company)
