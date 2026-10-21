"""Fetch sources, upsert jobs, detect closed postings, enrich descriptions, notify, and run on a schedule."""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from sqlmodel import select

from . import digest, feed, gmail, maintenance, settings
from .config import DATA
from .models import Job, session
from .sources import ashby, boards, enrich, greenhouse, lever, simplify_list, smartrecruiters, workable, workday

log = logging.getLogger("jobprep.refresh")
_lock = threading.Lock()
STATUS_FILE = DATA / "refresh_status.json"  # so a server restart doesn't trigger a full rescan


def _load_status() -> dict[str, dict]:
    try:
        raw = json.loads(STATUS_FILE.read_text())
        return {k: {**v, "running": False, "last_run": datetime.fromisoformat(v["last_run"])} for k, v in raw.items() if v.get("last_run")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _save_status() -> None:
    try:
        STATUS_FILE.write_text(json.dumps({k: v for k, v in status.items() if not v.get("running")}, default=str))
    except OSError:
        pass


status: dict[str, dict] = _load_status()  # source -> {last_run, running, added, closed, enriched, errors, error, boards}
ENRICH_PER_RUN = 800
ATS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday, "smartrecruiters": smartrecruiters,
       "workable": workable}


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": "JobPrep (personal job search)"}, follow_redirects=True,
                        limits=httpx.Limits(max_connections=16))


def _upsert(source: str, jobs: list[Job], scopes: set[str] | None, now: datetime) -> tuple[list[str], int]:
    """Insert/update `jobs`; mark open jobs of `source` that weren't returned as closed.
    `scopes` limits closing to jobs from these boards (None = the fetch covered the whole source)."""
    added, seen = [], set()
    with session() as s:
        for j in jobs:
            if j.id in seen:
                continue
            seen.add(j.id)
            old = s.get(Job, j.id)
            if old is None:
                j.first_seen = j.last_seen = now
                s.add(j)
                added.append(j.id)
                continue
            old.title, old.url = j.title, j.url
            old.location = j.location or old.location  # enrichment may have filled a better one
            old.posted_at = min(old.posted_at, j.posted_at, key=_aware)  # day-granular sources mustn't drift
            old.meta = {**old.meta, **j.meta}
            if j.description:
                old.description = j.description
            old.last_seen, old.closed = now, False
            s.add(old)
        closed = 0
        for j in s.exec(select(Job).where(Job.source == source, Job.closed == False)).all():  # noqa: E712
            in_scope = scopes is None or (j.meta.get("board") or j.company) in scopes
            if in_scope and j.id not in seen:
                j.closed = True
                s.add(j)
                closed += 1
        s.commit()
    return added, closed


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _enrich(client: httpx.Client) -> int:
    with session() as s:
        todo = [j for j in s.exec(select(Job).where(Job.closed == False, Job.description == "")).all()  # noqa: E712
                if not j.meta.get("no_description") and enrich.supported(j.url)]
    todo.sort(key=lambda j: _aware(j.posted_at), reverse=True)  # newest first: they matter most
    todo = todo[:ENRICH_PER_RUN]
    e = enrich.Enricher(client)

    def one(j: Job):
        try:
            return j.id, e.details(j.url)
        except (httpx.HTTPError, ValueError, KeyError):
            return j.id, None  # transient or odd payload; retry next run

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(one, todo))
    done = 0
    with session() as s:
        for job_id, d in results:
            if d is None:
                continue
            j = s.get(Job, job_id)
            if d.get("description"):
                j.description = d["description"]
                j.location = d.get("location") or j.location
                done += 1
            else:
                j.meta = {**j.meta, "no_description": True}  # gone from the ATS; don't retry
            s.add(j)
        s.commit()
    return done


def _fetch_ats(client: httpx.Client, result: dict) -> dict[str, tuple[list[Job], set[str]]]:
    targets = [(src, b, name) for src, bs in boards.all_boards().items() for b, name in bs.items()]
    result["boards"] = len(targets)

    def one(t):
        src, b, name = t
        try:
            return src, b, ATS[src].fetch(b, client, name)
        except (httpx.HTTPError, ValueError, KeyError):
            return src, b, None

    out = {src: ([], set()) for src in ATS}
    with ThreadPoolExecutor(8) as pool:
        for src, b, jobs in pool.map(one, targets):
            if jobs is None:
                result["errors"] += 1
                continue
            out[src][0].extend(jobs)
            out[src][1].add(b)
    return out


def _notify(new_ids: list[str]) -> None:
    st = settings.get()
    if not (st.notify and new_ids and sys.platform == "darwin"):
        return
    ids = set(new_ids)
    hits = [x for x in feed.build(st) if x["id"] in ids and not x["hidden_reasons"] and x["score"] >= st.notify_min_score]
    if not hits:
        return
    top = hits[0]
    more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
    msg = f"{top['title']} · {top['company']} · score {top['score']:g}{more}"
    subprocess.run(["osascript", "-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "JobPrep: new match"',
                    "-e", "end run", msg], check=False, capture_output=True)


def run(source: str) -> dict:
    """Refresh "simplify" (the SimplifyJobs list) or "ats" (every company job board)."""
    if source not in ("simplify", "ats"):
        return {"error": f"unknown source {source}"}
    now = datetime.now(timezone.utc)
    result = {"last_run": now, "running": True, "added": 0, "closed": 0, "enriched": 0, "errors": 0, "error": None}
    status[source] = {**status.get(source, {}), "running": True}
    added: list[str] = []
    first_import = False
    with _lock, _client() as client:
        try:
            if source == "simplify":
                with session() as s:
                    first_import = s.exec(select(Job.id).where(Job.source == "simplify").limit(1)).first() is None
                added, result["closed"] = _upsert(source, simplify_list.fetch(client), None, now)
            else:
                with session() as s:
                    first_import = s.exec(select(Job.id).where(Job.source.in_(list(ATS))).limit(1)).first() is None
                for src, (jobs, ok) in _fetch_ats(client, result).items():
                    a, c = _upsert(src, jobs, ok, now)
                    added += a
                    result["closed"] += c
            result["added"] = len(added)
            result["enriched"] = _enrich(client)
        except (httpx.HTTPError, ValueError) as e:
            result["error"] = str(e)
        feed.invalidate()
    result["running"] = False
    status[source] = result
    _save_status()
    if not first_import:  # the very first import is thousands of "new" jobs; don't alert on it
        _notify(added)
    feed.build(settings.get())  # warm the cache so the next page load is instant
    return result


def run_async(source: str = "all") -> dict:
    sources = ["simplify", "ats"] if source == "all" else [source]
    if any(status.get(s, {}).get("running") for s in sources):
        return {"started": False, "reason": "already running"}
    threading.Thread(target=lambda: [run(s) for s in sources], name="jobprep-refresh", daemon=True).start()
    return {"started": True, "sources": sources}


def _due(source: str, minutes: int) -> bool:
    last = status.get(source, {}).get("last_run")
    return minutes > 0 and (last is None or (datetime.now(timezone.utc) - last).total_seconds() >= minutes * 60)


def _loop() -> None:
    while True:
        st = settings.get()
        for source, minutes in (("simplify", st.refresh_simplify_minutes), ("ats", st.refresh_ats_minutes)):
            if _due(source, minutes):
                try:
                    run(source)
                except Exception:  # never let one bad run kill the scheduler
                    log.exception("refresh %s failed", source)
        if st.digest and digest.due(status.get("digest"), st.digest_hour):
            try:
                status["digest"] = digest.notify()
            except Exception:
                log.exception("digest failed")
            _save_status()
        if _due("maintenance", 24 * 60):
            try:
                status["maintenance"] = {"last_run": datetime.now(timezone.utc), "pruned": maintenance.prune(st.prune_closed_days),
                                         "backup": maintenance.backup() if st.weekly_backup else None}
                feed.invalidate()
            except Exception as e:
                status["maintenance"] = {"last_run": datetime.now(timezone.utc), "error": str(e)}
            _save_status()
        if gmail.address() and _due("gmail", st.gmail_scan_minutes):
            try:
                status["gmail"] = {"last_run": datetime.now(timezone.utc), **gmail.scan()}
            except Exception as e:
                status["gmail"] = {"last_run": datetime.now(timezone.utc), "error": str(e)}
            _save_status()
        time.sleep(60)


def start_scheduler() -> None:
    if os.environ.get("JOBPREP_NO_SCHEDULER"):
        return
    maintenance.migrate_settings()
    threading.Thread(target=lambda: feed.build(settings.get()), name="jobprep-warm", daemon=True).start()
    threading.Thread(target=_loop, name="jobprep-scheduler", daemon=True).start()
