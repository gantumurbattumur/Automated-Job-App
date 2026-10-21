"""Application tracker: status changes, auto-ghosting, manual entries, stats, CSV."""
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from .models import PROGRESS, STATUSES, Application, Job, session

GHOST_AFTER_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def set_status(job_id: str, status: str, source: str = "manual", notes: str | None = None,
               follow_up: str | None = None, forward_only: bool = False, cpt_issue: bool | None = None) -> Application:
    """Update one application. `forward_only` (for automatic updates) never moves an application backwards,
    e.g. a late "thanks for applying" email won't undo "interview"."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    now = _now()
    with session() as s:
        if not s.get(Job, job_id):
            raise KeyError(job_id)
        existing = s.get(Application, job_id)
        a = existing or Application(job_id=job_id)
        cur = existing.status if existing else None
        terminal = cur in ("offer", "rejected", "withdrawn")
        backwards = cur in PROGRESS and status in PROGRESS and PROGRESS[status] < PROGRESS[cur]
        if not (forward_only and cur and (backwards or (terminal and status != "offer"))):
            a.status = status
        if status == "opened" and not a.opened_at:
            a.opened_at = now
        if status in ("applied", "oa", "interview", "offer", "rejected") and not a.applied_at:
            a.applied_at = now
        a.updated_at, a.source = now, source if a.status == status else a.source
        if notes is not None:
            a.notes = notes
        if follow_up is not None:
            a.follow_up = follow_up or None
        if cpt_issue is not None:
            a.cpt_issue = cpt_issue
        s.add(a)
        s.commit()
        s.refresh(a)
        return a


def add_manual(company: str, title: str, url: str = "", status: str = "applied", notes: str = "") -> Application:
    """Track an application for a job the app never fetched (applied elsewhere, or found by the Gmail scan)."""
    jid = f"manual:{uuid.uuid4().hex[:12]}"
    with session() as s:  # closed=True keeps it out of the job feed
        s.add(Job(id=jid, source="manual", company=company, title=title or "(unknown role)", url=url,
                  posted_at=_now(), closed=True))
        s.commit()
    return set_status(jid, status, "manual", notes=notes)


def _ghost(s) -> None:
    cutoff = _now() - timedelta(days=GHOST_AFTER_DAYS)
    for a in s.exec(select(Application).where(Application.status == "applied")).all():
        if _aware(a.updated_at or a.applied_at) and _aware(a.updated_at or a.applied_at) < cutoff:
            a.status, a.updated_at, a.source = "ghosted", _now(), "auto"
            s.add(a)
    s.commit()


def rows() -> list[dict]:
    with session() as s:
        _ghost(s)
        out = []
        for a in s.exec(select(Application)).all():
            j = s.get(Job, a.job_id)
            out.append({**a.model_dump(), "company": j.company, "title": j.title, "url": j.url, "location": j.location,
                        "job_source": j.source})
    return sorted(out, key=lambda r: _aware(r["updated_at"] or r["applied_at"] or r["opened_at"]) or _now(), reverse=True)


def stats() -> dict:
    rs = rows()
    by = {s: 0 for s in STATUSES}
    for r in rs:
        by[r["status"]] += 1
    applied = [r for r in rs if r["applied_at"]]
    week = _now() - timedelta(days=7)
    responded = sum(by[s] for s in ("oa", "interview", "offer", "rejected"))
    return {"by_status": by, "applied_total": len(applied),
            "applied_last_7d": sum(1 for r in applied if _aware(r["applied_at"]) >= week),
            "response_rate": round(responded / len(applied), 2) if applied else 0,
            "follow_ups_due": sum(1 for r in rs if r["follow_up"] and r["follow_up"] <= _now().date().isoformat()
                                  and r["status"] in ("applied", "oa", "interview"))}


def to_csv() -> str:
    buf = io.StringIO()
    cols = ["company", "title", "status", "applied_at", "opened_at", "updated_at", "follow_up", "source", "cpt_issue", "notes",
            "url", "location"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows())
    return buf.getvalue()
