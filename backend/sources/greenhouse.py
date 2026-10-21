import html
import re
from datetime import datetime, timezone

import httpx

from ..models import Job
from .common import is_intern

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def _text(raw: str) -> str:
    raw = html.unescape(raw or "")
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse(slug: str, payload: dict, company: str | None = None, interns_only: bool = False) -> list[Job]:
    jobs = []
    for j in payload.get("jobs", []):
        if interns_only and not is_intern(j.get("title", "")):
            continue
        jobs.append(Job(
            id=f"greenhouse:{slug}:{j['id']}",
            source="greenhouse",
            company=company or slug,
            title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            description=_text(j.get("content", "")),  # absent in the list call; filled in by enrichment
            posted_at=_dt(j.get("first_published") or j.get("updated_at")),
            meta={"board": slug},
        ))
    return jobs


def fetch(slug: str, client: httpx.Client, company: str | None = None) -> list[Job]:
    r = client.get(API.format(slug=slug), timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return parse(slug, r.json(), company, interns_only=True)
