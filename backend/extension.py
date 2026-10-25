"""Data for the JobPrep Chrome extension: everything a page needs to fill an application, and recording the result.

The extension fills forms inside your own Chrome tab; this module only answers "what should go in this form?"
from your profile, canned answers and resumes, all local.
"""
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from sqlmodel import select

from . import autofill, feed, profile, relevance, resume, settings, tracker
from .models import Job, session

# where an application form lives relative to the posting URL we stored
APPLY_SUFFIX = re.compile(r"/(apply|application|applications?/new)(/.*)?$", re.I)
WORKDAY_REQ = re.compile(r"_((?:J?R)-?\d{4,}(?:-\d+)?)", re.I)
runs: list[dict] = []  # recent extension activity, newest first (for the Settings page)


def _norm(url: str) -> str:
    u = urlparse(url.strip())
    path = APPLY_SUFFIX.sub("", u.path.rstrip("/"))
    path = re.sub(r"^/[a-z]{2}-[A-Z]{2}(?=/)", "", path)  # Workday locale prefix /en-US/
    return f"{u.netloc.lower()}{path.lower()}"


def match_job(url: str) -> Job | None:
    """The stored posting this application page belongs to (posting URL, its /apply page, or a Workday req id)."""
    if not url:
        return None
    key = _norm(url)
    gh = parse_qs(urlparse(url).query).get("gh_jid")  # company-hosted Greenhouse embeds
    with session() as s:
        if gh:
            j = s.exec(select(Job).where(Job.id.like(f"greenhouse:%:{gh[0]}"))).first()
            if j:
                return j
        tail = key.split("/", 1)[-1][-60:]  # the distinctive end of the path
        cands = s.exec(select(Job).where(Job.url.like(f"%{tail}%"))).all() if len(tail) > 6 else []
        for j in cands:
            if _norm(j.url) == key or key.startswith(_norm(j.url)):
                return j
        if m := WORKDAY_REQ.search(url):
            return s.exec(select(Job).where(Job.source.in_(["workday", "simplify"]), Job.url.like(f"%{m[1]}%"))).first()
    return None


def context(url: str, page_company: str = "", page_title: str = "") -> dict:
    p = profile.get()
    job = match_job(url)
    company = job.company if job else page_company.strip()
    row = next((r for r in feed.build_cached(settings.get()) if job and r["id"] == job.id), {}) if job else {}
    lane = row.get("best_lane") if row.get("best_lane") in resume.names() else resume.primary()
    essay = ""
    if job and job.description:
        try:
            essay = relevance.claude_prompt(job, relevance.resume_help(job), resume.text(lane))
        except Exception:  # resume help needs the embedding model; the form still fills without it
            essay = ""
    if not essay:
        essay = (f"Help me answer an application question for {page_title or 'this internship'} at {company or 'this company'}. "
                 f"Use only facts from my resume below; keep it under 150 words.\n\nMY RESUME:\n{resume.text(lane)[:6000]}")
    return {
        "job": {"id": job.id, "title": job.title, "company": job.company, "url": job.url} if job else None,
        "company": company,
        "values": {k: v for k, v in autofill.values(p).items() if isinstance(v, str)},
        "fields": [[pat, key] for pat, key in autofill.FIELDS],
        "answers": profile.answers_for(company or "this company", p),
        "worked_here": profile.worked_at(company, p) if company else False,
        "resume": {"lane": lane, "label": resume.config()["lanes"].get(lane, {}).get("label", lane),
                   "url": f"/resume/{lane}", "filename": resume.path(lane).name},
        "essay_prompt": essay,
        "confirm": autofill.CONFIRM.pattern,
        "decline": autofill.DECLINE.pattern,
        "states": autofill.STATES,
    }


def record(url: str, status: str, filled: int, missing: list[str], job_id: str | None, company: str, title: str) -> dict:
    """Log an extension run. "opened" updates a job JobPrep already knows; "applied" also creates a tracker entry
    for an unknown page (so error pages or forms you only looked at don't clutter the tracker)."""
    if job_id:
        with session() as s:
            job_id = job_id if s.get(Job, job_id) else None
    if not job_id:
        job = match_job(url)
        job_id = job.id if job else None
    if not job_id and status == "applied":
        with session() as s:  # one manual entry per page, not one per click
            existing = s.exec(select(Job).where(Job.source == "manual", Job.url == url)).first()
        job_id = existing.id if existing else \
            tracker.add_manual(company or urlparse(url).netloc, title or "(unknown role)", url, status).job_id
    if job_id and status in ("opened", "applied"):
        tracker.set_status(job_id, status, "extension", forward_only=True)
    entry = {"at": datetime.now(timezone.utc), "url": url, "status": status, "filled": filled, "missing": missing[:20],
             "job_id": job_id, "company": company, "title": title}
    runs.insert(0, entry)
    del runs[20:]
    return entry
