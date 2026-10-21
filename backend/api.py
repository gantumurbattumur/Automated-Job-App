import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from . import digest, feed, gmail, maintenance, profile, settings, tracker
from . import refresh as refresh_mod
from .config import DATA, ROOT, load
from .models import Application, InboxItem, Job, init_db, session
from .settings import Settings

app = FastAPI(title="Job Prep")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()
    refresh_mod.start_scheduler()


@app.post("/refresh")
def refresh(source: str = "all", wait: bool = False):
    """Starts in the background (polling ~900 boards takes minutes); watch /status. `wait` runs inline."""
    if wait:
        return {s: refresh_mod.run(s) for s in (["simplify", "ats"] if source == "all" else [source])}
    return refresh_mod.run_async(source)


@app.get("/status")
def status():
    return refresh_mod.status


@app.get("/jobs")
def jobs(show_hidden: bool = False, limit: int = 300):
    ranked = feed.build_cached(settings.get())
    shown = [r for r in ranked if not r["hidden_reasons"]]
    hidden = [r for r in ranked if r["hidden_reasons"]]
    return {"shown": shown[:limit], "shown_count": len(shown), "hidden_count": len(hidden),
            "hidden": hidden[:limit] if show_hidden else []}


@app.get("/settings")
def get_settings() -> Settings:
    return settings.get()


@app.put("/settings")
def put_settings(s: Settings) -> Settings:
    return settings.save(s)


@app.delete("/settings")
def reset_settings() -> Settings:
    return settings.reset()


@app.get("/settings/defaults")
def settings_defaults() -> Settings:
    return Settings()


@app.get("/settings/options")
def options():
    return {"lanes": ["auto", *load("resumes")["lanes"]]}


@app.get("/presets")
def get_presets():
    return settings.presets()


@app.put("/presets/{name}")
def put_preset(name: str, s: Settings):
    return settings.save_preset(name, s)


@app.delete("/presets/{name}")
def delete_preset(name: str):
    return settings.delete_preset(name)


@app.get("/profile")
def get_profile() -> profile.Profile:
    return profile.get()


@app.put("/profile")
def put_profile(p: profile.Profile) -> profile.Profile:
    return profile.save(p)


@app.get("/jobs/{job_id}/packet")
def packet(job_id: str):
    """Everything needed to fill this application: resume to upload, skill gaps, your info, canned answers."""
    with session() as s:
        j = s.get(Job, job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    row = next((x for x in feed.build_cached(settings.get()) if x["id"] == job_id), {})
    lanes = load("resumes")["lanes"]
    lane = row.get("best_lane") if row.get("best_lane") in lanes else load("resumes")["primary"]
    p = profile.get()
    return {"job": {"id": j.id, "company": j.company, "title": j.title, "url": j.url},
            "resume": {"lane": lane, "url": f"/resume/{lane}", "file": Path(lanes[lane]).name},
            "skills": {"matched": row.get("skills_matched", []), "missing": row.get("skills_missing", [])},
            "worked_here": profile.worked_at(j.company, p),
            "fields": profile.fields(p), "answers": profile.answers_for(j.company, p)}


@app.get("/jobs/{job_id}")
def job_detail(job_id: str):
    """One feed row plus the full description (the list leaves descriptions out to stay small)."""
    with session() as s:
        j = s.get(Job, job_id)
        a = s.get(Application, job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    row = next((x for x in feed.build_cached(settings.get()) if x["id"] == job_id or
                any(o["id"] == job_id for o in x.get("also", []))), None)
    return {**(row or {"id": j.id, "company": j.company, "title": j.title, "url": j.url, "location": j.location,
                       "hidden_reasons": ["closed or not in the current feed"]}),
            "description": j.description, "closed": j.closed, "application": a}


@app.get("/home")
def home():
    """Everything the Home page shows, in one call."""
    st = settings.get()
    rows = feed.build_cached(st)
    shown = [r for r in rows if not r["hidden_reasons"]]
    day = datetime.now(timezone.utc).timestamp() - 86400
    with session() as s:
        inbox_n = len(s.exec(select(InboxItem).where(InboxItem.state == "new")).all())
    apps = tracker.rows()
    today = datetime.now().date().isoformat()
    return {"shown": len(shown), "new_today": sum(1 for r in shown if r["posted_at"].timestamp() >= day
                                                  or r["first_seen"].replace(tzinfo=timezone.utc).timestamp() >= day),
            "cpt_friendly": sum(1 for r in shown if r["cpt"] == "friendly" or (r["h1b_approvals"] or 0) >= st.h1b_min_approvals),
            "closing_soon": [r for r in shown if r["days_left"] is not None and 0 <= r["days_left"] <= 7][:8],
            "top": digest.top(24, 10) or shown[:10], "stats": tracker.stats(), "inbox": inbox_n,
            "follow_ups": [a for a in apps if a["follow_up"] and a["follow_up"] <= today and a["status"] in ("applied", "oa", "interview")],
            "recent": apps[:6], "personal": feed.personal_info(), "sources": refresh_mod.status,
            "gmail": bool(gmail.address())}


@app.get("/jobs/{job_id}/resume-help")
def resume_help(job_id: str):
    from . import relevance, resume
    with session() as s:
        j = s.get(Job, job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    h = relevance.resume_help(j)
    h["prompt"] = relevance.claude_prompt(j, h, resume.lane_texts()[h["lane"]])
    return h


@app.get("/digest")
def get_digest(hours: int = 24, n: int = 10):
    return {"jobs": digest.top(hours, n), "last_sent": refresh_mod.status.get("digest")}


@app.get("/personal")
def personal_model():
    return feed.personal_info()


@app.post("/maintenance/backup")
def backup_now():
    return {"path": maintenance.backup(force=True)}


@app.get("/resume/{lane}")
def resume_file(lane: str):
    lanes = load("resumes")["lanes"]
    if lane not in lanes:
        raise HTTPException(404, "unknown resume")
    return FileResponse(ROOT / lanes[lane], media_type="application/pdf", filename=Path(lanes[lane]).name)


class AppUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    follow_up: str | None = None
    cpt_issue: bool | None = None
    source: str = "manual"


class ManualApp(BaseModel):
    company: str
    title: str = ""
    url: str = ""
    status: str = "applied"
    notes: str = ""


@app.get("/applications")
def applications():
    return tracker.rows()


@app.get("/applications/stats")
def application_stats():
    return tracker.stats()


@app.get("/applications.csv")
def applications_csv():
    return Response(tracker.to_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=applications.csv"})


@app.put("/applications/{job_id}")
def upsert_application(job_id: str, body: AppUpdate | None = None, status: str | None = None):
    body = body or AppUpdate()
    st = body.status or status or "applied"  # ?status= kept for simple buttons
    try:
        return tracker.set_status(job_id, st, body.source, body.notes, body.follow_up, cpt_issue=body.cpt_issue)
    except KeyError:
        raise HTTPException(404, "unknown job")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/applications/manual")
def manual_application(body: ManualApp):
    try:
        return tracker.add_manual(body.company, body.title, body.url, body.status, body.notes)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/applications/{job_id}")
def delete_application(job_id: str):
    with session() as s:
        if a := s.get(Application, job_id):
            s.delete(a)
            s.commit()
    return {"deleted": job_id}


@app.post("/autofill/{job_id:path}")
def autofill(job_id: str):
    """Open a visible browser that fills this application and stops before Submit."""
    with session() as s:
        j = s.get(Job, job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {"started": False, "error": "Playwright isn't installed: .venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium"}
    log = open(DATA / "autofill.log", "a")
    subprocess.Popen([sys.executable, "-m", "backend.autofill", job_id], cwd=ROOT, stdout=log, stderr=log)
    return {"started": True, "title": j.title, "company": j.company}


class GmailConnect(BaseModel):
    email: str
    app_password: str


@app.get("/gmail")
def gmail_status():
    return {"connected": bool(gmail.address()), "email": gmail.address(), "last_scan": refresh_mod.status.get("gmail")}


@app.post("/gmail/connect")
def gmail_connect(body: GmailConnect):
    try:
        gmail.connect(body.email.strip(), body.app_password)
    except Exception as e:  # imaplib raises its own error types
        raise HTTPException(400, f"Gmail login failed: {e}")
    return gmail_status()


@app.delete("/gmail")
def gmail_disconnect():
    gmail.disconnect()
    return gmail_status()


@app.post("/gmail/scan")
def gmail_scan(days: int = 14):
    r = gmail.scan(days)
    refresh_mod.status["gmail"] = {"last_run": datetime.now(timezone.utc), **r}
    return r


@app.get("/inbox")
def inbox(state: str = "new"):
    """Job emails, with the employer and role read from the email and the jobs it most likely belongs to."""
    with session() as s:
        items = s.exec(select(InboxItem).where(InboxItem.state == state)).all()
        linked = {i.job_id: s.get(Job, i.job_id) for i in items if i.job_id}
    out = []
    for i in sorted(items, key=lambda x: x.received_at, reverse=True):
        company = gmail.guess_company(i.sender, i.subject, i.snippet) or i.company_guess
        role = i.role_guess or gmail.extract_role(i.subject, i.snippet) or ""
        j = linked.get(i.job_id)
        out.append({**i.model_dump(), "company_guess": company, "role_guess": role,
                    "linked": {"company": j.company, "title": j.title} if j else None,
                    "suggestions": gmail.suggestions(company, role) if state == "new" else []})
    return out


@app.get("/jobs-search")
def jobs_search(q: str, limit: int = 15):
    """Typeahead over every job in the database (tracked ones first) by company and title words."""
    words = [w for w in q.lower().split() if w]
    if not words:
        return []
    with session() as s:
        tracked = {a.job_id: a.status for a in s.exec(select(Application)).all()}
        jobs = s.exec(select(Job)).all()
    hits = [j for j in jobs if all(w in f"{j.company} {j.title}".lower() for w in words)]
    hits.sort(key=lambda j: (j.id not in tracked, j.closed, -j.posted_at.timestamp()))
    return [{"job_id": j.id, "company": j.company, "title": j.title, "location": j.location, "tracked": tracked.get(j.id),
             "closed": j.closed} for j in hits[:limit]]


class InboxResolve(BaseModel):
    job_id: str | None = None  # attach to an existing tracked job…
    company: str | None = None  # …or create a manual entry
    title: str = ""


@app.post("/inbox/{message_id:path}/resolve")
def inbox_resolve(message_id: str, body: InboxResolve):
    with session() as s:
        item = s.get(InboxItem, message_id)
    if not item:
        raise HTTPException(404, "unknown email")
    if body.job_id:
        tracker.set_status(body.job_id, item.kind, "gmail", forward_only=True)
        job_id = body.job_id
    else:
        company = body.company or gmail.guess_company(item.sender, item.subject, item.snippet) or item.company_guess or "Unknown"
        title = body.title or item.role_guess or gmail.extract_role(item.subject, item.snippet) or "(unknown role)"
        job_id = tracker.add_manual(company, title, "", item.kind, f"From email: {item.subject}").job_id
    with session() as s:
        item = s.get(InboxItem, message_id)
        item.job_id, item.state = job_id, "applied_to_tracker"
        s.add(item)
        s.commit()
    return {"job_id": job_id}


@app.post("/inbox/{message_id:path}/dismiss")
def inbox_dismiss(message_id: str):
    with session() as s:
        if item := s.get(InboxItem, message_id):
            item.state = "dismissed"
            s.add(item)
            s.commit()
    return {"dismissed": message_id}


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "frontend" / "index.html")
