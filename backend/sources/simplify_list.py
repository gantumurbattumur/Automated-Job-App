"""Community internship list maintained by SimplifyJobs on GitHub (updated many times a day)."""
from datetime import datetime, timezone

import httpx

from ..models import Job

URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
REQUIRED = {"id", "company_name", "title", "url", "date_posted", "active"}


def parse(listings: list[dict]) -> list[Job]:
    """Active, visible listings only; anything missing from the result is treated as closed."""
    if not isinstance(listings, list) or (listings and not REQUIRED <= set(listings[0])):
        raise ValueError("SimplifyJobs listings.json format changed")
    jobs = []
    for x in listings:
        if not x.get("active") or not x.get("is_visible", True):
            continue
        jobs.append(Job(
            id=f"simplify:{x['id']}",
            source="simplify",
            company=x["company_name"],
            title=x["title"],
            location="; ".join(x.get("locations") or []),
            url=x["url"],
            posted_at=datetime.fromtimestamp(x["date_posted"], timezone.utc),
            meta={k: x[k] for k in ("terms", "degrees", "sponsorship", "category") if x.get(k)},
        ))
    return jobs


def fetch(client: httpx.Client) -> list[Job]:
    r = client.get(URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return parse(r.json())
