"""Fill in descriptions (and real locations) for postings whose source gave only a title."""
import re

import httpx

from . import ashby, smartrecruiters, workable, workday
from .greenhouse import _text
from .lever import description as lever_description

GREENHOUSE = re.compile(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)/jobs/(\d+)")
LEVER = re.compile(r"https?://jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})")
ASHBY = re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")


def supported(url: str) -> bool:
    return any(p.match(url) for p in (GREENHOUSE, LEVER, ASHBY, workday.URL, smartrecruiters.URL, workable.URL)) and \
        bool(not workday.URL.match(url) or workday.URL.match(url)[4])


def api_url(url: str) -> str | None:
    if m := GREENHOUSE.match(url):
        return f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}"
    if m := LEVER.match(url):
        return f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}"
    return None


class Enricher:
    """One per refresh run; caches Ashby boards (Ashby has no per-posting API)."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self._ashby: dict[str, dict[str, dict]] = {}

    def details(self, url: str) -> dict | None:
        """{"description", maybe "location"}; {} if the posting is gone; None if unsupported."""
        c = self.client
        if m := ASHBY.match(url):
            if m[1] not in self._ashby:
                self._ashby[m[1]] = {p["id"]: p for p in ashby.board(m[1], c)}
            p = self._ashby[m[1]].get(m[2])
            return {"description": p.get("descriptionPlain", "")} if p else {}
        if workday.URL.match(url):
            return workday.detail(url, c)
        if smartrecruiters.URL.match(url):
            return smartrecruiters.detail(url, c)
        if workable.URL.match(url):
            return workable.detail(url, c)
        api = api_url(url)
        if not api:
            return None
        r = c.get(api, timeout=20)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        d = r.json()
        return {"description": _text(d["content"]) if "content" in d else lever_description(d)}
