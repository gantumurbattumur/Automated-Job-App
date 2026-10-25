import threading
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from . import digest, feed, geo, gmail, maintenance, resume, settings, tailor, tracker
from . import refresh as refresh_mod
from .config import ROOT
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
    """Starts in the background (polling ~900 boards takes minutes); watch /status. `wait` runs inline.
    source=morning runs the full morning routine (postings, boards, Gmail, cleanup, digest)."""
    if source == "morning":
        if refresh_mod.status.get("morning", {}).get("running"):
            return {"started": False, "reason": "already running"}
        threading.Thread(target=refresh_mod.morning_run, daemon=True).start()
        return {"started": True, "sources": ["morning"]}
    if wait:
        return {s: refresh_mod.run(s) for s in (["simplify", "ats"] if source == "all" else [source])}
    return refresh_mod.run_async(source)


@app.get("/status")
def status():
    st = settings.get()
    return {**refresh_mod.status, "schedule": {"mode": st.schedule, "daily_time": st.daily_time,
                                               "done_today": not refresh_mod.daily_due("00:00", refresh_mod.status.get("morning"))}}


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
    if s.near_location.strip():
        try:
            found = geo.center(s.near_location)
        except Exception as e:  # first use downloads the free city list; offline -> say so
            raise HTTPException(503, f"Couldn't load the city list for distance filtering: {e}")
        if not found:
            raise HTTPException(422, f"Couldn't find “{s.near_location}”. Try “City, ST”, e.g. “Los Angeles, CA”.")
    return settings.save(s)


@app.get("/geo/suggest")
def geo_suggest(q: str):
    return geo.suggest(q)


@app.delete("/settings")
def reset_settings() -> Settings:
    return settings.reset()


@app.get("/settings/defaults")
def settings_defaults() -> Settings:
    return Settings()


@app.get("/settings/options")
def options():
    return {"lanes": ["auto", *resume.names()],
            "labels": {n: l.get("label") or n for n, l in resume.config()["lanes"].items()}}


@app.get("/presets")
def get_presets():
    return settings.presets()


@app.put("/presets/{name}")
def put_preset(name: str, s: Settings):
    return settings.save_preset(name, s)


@app.delete("/presets/{name}")
def delete_preset(name: str):
    return settings.delete_preset(name)


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


@app.get("/digest")
def get_digest(hours: int = 24, n: int = 10):
    return {"jobs": digest.top(hours, n), "last_sent": refresh_mod.status.get("digest")}


@app.get("/personal")
def personal_model():
    return feed.personal_info()


@app.post("/maintenance/backup")
def backup_now():
    return {"path": maintenance.backup(force=True)}


def _top(n: int = 50) -> list[dict]:
    return [r for r in feed.build(settings.get()) if not r["hidden_reasons"]][:n]


def _impact(before: list[dict]) -> dict:
    """How a resume change moved your top matches (same filters, re-ranked with the new text)."""
    after = _top(len(before) or 50)
    b_ids, a_ids = {r["id"] for r in before}, {r["id"] for r in after}
    avg = lambda rows, k: round(sum(r[k] for r in rows) / len(rows), 1 if k == "score" else 3) if rows else 0  # noqa: E731
    pick = lambda r: {"id": r["id"], "title": r["title"], "company": r["company"], "score": r["score"]}  # noqa: E731
    return {"top_n": len(after), "avg_score_before": avg(before, "score"), "avg_score_after": avg(after, "score"),
            "avg_match_before": avg(before, "relevance"), "avg_match_after": avg(after, "relevance"),
            "entered": [pick(r) for r in after if r["id"] not in b_ids][:8],
            "left": [pick(r) for r in before if r["id"] not in a_ids][:8]}


def _resume_view(name: str, lane: dict, primary: str) -> dict:
    from . import relevance
    p = resume.path(name)
    exists = p.exists()
    text = resume.text(name) if exists else ""
    return {"name": name, "label": lane.get("label") or name, "primary": name == primary, "file": p.name, "exists": exists,
            "uploaded_at": lane.get("uploaded_at"), "size_kb": round(p.stat().st_size / 1024) if exists else 0,
            "pages": resume.pages(p) if exists else None, "words": len(text.split()),
            "skills": sorted(relevance.skills_in(text)), "targets": lane.get("targets") or [], "versions": resume.versions(name),
            "preview": text[:1200]}


@app.get("/resumes")
def list_resumes():
    cfg = resume.config()
    return {"primary": cfg["primary"], "resumes": [_resume_view(n, l, cfg["primary"]) for n, l in cfg["lanes"].items()]}


def _resume_change(fn):
    before = _top()
    try:
        fn()
    except KeyError:
        raise HTTPException(404, "unknown resume or version")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {**list_resumes(), "impact": _impact(before)}


@app.post("/resumes")
async def add_resume(label: str = Form(...), file: UploadFile = File(...), targets: str = Form("")):
    data = await file.read()
    return _resume_change(lambda: resume.add(label, file.filename or "resume.pdf", data, [t for t in targets.split(",") if t.strip()]))


@app.put("/resumes/{lane}/file")
async def replace_resume(lane: str, file: UploadFile = File(...)):
    data = await file.read()
    return _resume_change(lambda: resume.replace(lane, file.filename or "resume.pdf", data))


class ResumeUpdate(BaseModel):
    label: str | None = None
    targets: list[str] | None = None
    primary: bool = False


@app.put("/resumes/{lane}")
def update_resume(lane: str, body: ResumeUpdate):
    return _resume_change(lambda: resume.update(lane, body.label, body.targets, body.primary))


@app.post("/resumes/{lane}/revert")
def revert_resume(lane: str, version: str):
    return _resume_change(lambda: resume.revert(lane, version))


@app.delete("/resumes/{lane}")
def delete_resume(lane: str):
    return _resume_change(lambda: resume.delete(lane))


@app.get("/resume/{lane}")
def resume_file(lane: str):
    if lane not in resume.names():
        raise HTTPException(404, "unknown resume")
    p = resume.path(lane)
    media = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    return FileResponse(p, media_type=media.get(p.suffix.lower(), "text/plain"), filename=p.name)


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


# --- resume tailoring (up to 10 jobs) ----------------------------------------------

class TailorAdd(BaseModel):
    job_id: str | None = None
    company: str = ""
    title: str = ""
    url: str = ""
    jd_text: str = ""


class TailorPaste(BaseModel):
    text: str


class TailorDecision(BaseModel):
    status: str
    text: str | None = None


class TailorUpdate(BaseModel):
    lane: str = ""


def _tailor(fn):
    try:
        return fn()
    except KeyError:
        raise HTTPException(404, "not in your tailoring list")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/tailor")
def tailor_list():
    out = []
    for t in tailor.items():
        try:
            r = tailor.report(t.id)
        except Exception as e:  # one broken description shouldn't hide the rest
            out.append({"id": t.id, "title": t.title, "company": t.company, "error": str(e)})
            continue
        sug = r["data"].get("suggestions", [])
        out.append({"id": t.id, "title": t.title, "company": t.company, "url": t.url, "job_id": t.job_id, "lane": r["lane"],
                    "baseline": r["baseline"], "score": r["now"]["score"],
                    "with_accepted": r["with_accepted"]["score"] if r["with_accepted"] else None,
                    "with_all": r["with_all"]["score"] if r["with_all"] else None,
                    "suggestions": len(sug), "accepted": sum(x["status"] == "accepted" for x in sug)})
    return {"items": out, "max": tailor.MAX_ITEMS}


@app.post("/tailor")
def tailor_add(body: TailorAdd):
    return _tailor(lambda: {"id": tailor.add(body.job_id, body.company, body.title, body.jd_text, body.url)})


@app.get("/tailor/{item_id}")
def tailor_report(item_id: str):
    return _tailor(lambda: tailor.report(item_id))


@app.get("/tailor/{item_id}/prompt")
def tailor_prompt(item_id: str):
    return _tailor(lambda: {"prompt": tailor.prompt(item_id)})


@app.post("/tailor/{item_id}/suggestions")
def tailor_import(item_id: str, body: TailorPaste):
    return _tailor(lambda: tailor.import_suggestions(item_id, body.text))


@app.put("/tailor/{item_id}/suggestions/{sug_id}")
def tailor_decide(item_id: str, sug_id: str, body: TailorDecision):
    return _tailor(lambda: tailor.decide(item_id, sug_id, body.status, body.text))


@app.put("/tailor/{item_id}")
def tailor_update(item_id: str, body: TailorUpdate):
    def go():
        t = tailor.get(item_id)
        if not t:
            raise KeyError(item_id)
        t.lane = body.lane
        tailor.save(t)
        return tailor.report(item_id)
    return _tailor(go)


@app.delete("/tailor/{item_id}")
def tailor_remove(item_id: str):
    tailor.remove(item_id)
    return {"removed": item_id}


@app.get("/tailor/{item_id}/export")
def tailor_export(item_id: str):
    return Response(_tailor(lambda: tailor.export(item_id)), media_type="text/plain; charset=utf-8")


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
