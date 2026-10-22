"""Rank + filter the job feed. Relevance and classification are cached until jobs or rules change."""
import json
import re
import threading
from datetime import datetime, timezone

from sqlmodel import select

from . import geo, h1b, personal, ranking, relevance, resume
from .config import ROOT, load
from .filters import _word, apply, classify, parse_deadline
from .models import Application, Job, session
from .settings import Settings

_lock = threading.Lock()
# rel: per-lane relevance, rebuilt when jobs change (cheap: vectors/skills are cached below it)
# flags: per-job classification keyed by a fingerprint of its text, so a refresh only redoes changed jobs
_cache: dict = {"version": None, "rel": {}, "flags": {}, "rules_mtime": None}
_jobs_version = 0


def invalidate() -> None:
    global _jobs_version
    _jobs_version += 1


def _version() -> tuple:
    return _jobs_version, (ROOT / "config" / "rules.yaml").stat().st_mtime


def _key(j: Job) -> tuple:
    """Same posting across sources/locations: normalized company + title."""
    company = re.sub(r"\b(inc|llc|corp|corporation|co|ltd)\b|[^a-z0-9]", "", j.company.lower())
    t = re.sub(r"\([^)]*\)", " ", j.title.lower())  # "(Summer 2027)", "(Remote)"
    t = re.sub(r"\b(summer|fall|autumn|spring|winter)?\s*20\d\d\b|\bstart\b", " ", t)  # "Summer 2027:" prefixes
    title = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return company, title


def _sig(j: Job) -> int:
    return hash((j.title, j.company, j.description, j.location, json.dumps(j.meta, sort_keys=True)))


def _analyze(jobs: list[Job], st: Settings):
    rules = load("rules")
    with _lock:
        if _cache["version"] != _version():
            _cache.update(version=_version(), rel={})
        if _cache["rules_mtime"] != _version()[1]:  # rules changed: every classification is stale
            _cache.update(rules_mtime=_version()[1], flags={})
        cached = _cache["flags"]
        flags = {}
        for j in jobs:
            sig = _sig(j)
            hit = cached.get(j.id)
            if not hit or hit[0] != sig:
                hit = cached[j.id] = (sig, classify(j.title, j.company, j.description, j.location, rules, j.meta))
            flags[j.id] = hit[1]
        forced = st.lane if st.lane in resume.names() else None
        if forced not in _cache["rel"]:
            _cache["rel"][forced] = relevance.score(jobs, resume.primary(), rules["lane_margin"], forced)
        rel = _cache["rel"][forced]
    return flags, rel


def _friendliness(cpt: str, h1b: int | None, cpt_issue: bool, st: Settings) -> tuple[float, str]:
    """0-1 CPT-friendliness for ranking and a short label for the card."""
    if cpt_issue and st.cpt_issue_companies != "ignore":
        return 0.0, "rejected you over CPT before"
    if cpt == "unclear" and st.h1b_boost and (h1b or 0) >= st.h1b_min_approvals:
        return 0.75, f"likely friendly: {h1b:,} H-1B approvals"
    return ranking.FRIENDLINESS[cpt], {"friendly": "says CPT/F-1 OK", "unclear": "posting doesn't say", "blocked": "no CPT"}[cpt]


def build(st: Settings, now: datetime | None = None) -> list[dict]:
    """Every open job, scored, with `hidden_reasons`; sorted shown-first, then by score. Duplicates collapse."""
    now = now or datetime.now(timezone.utc)
    with session() as s:
        jobs = s.exec(select(Job).where(Job.closed == False)).all()  # noqa: E712
        app_rows = s.exec(select(Application)).all()
    apps = {a.job_id: a.status for a in app_rows}
    applied_ids = {k for k, v in apps.items() if v not in ("opened", "dismissed")}  # opened-but-not-applied stays in the feed
    applied_companies = {_key(j)[0] for j in jobs if apps.get(j.id) in ("applied", "oa", "interview", "offer", "rejected")}
    with session() as s:  # companies that turned you down over CPT (tagged in the tracker), incl. manual entries
        cpt_issue = {_key(s.get(Job, a.job_id))[0] for a in app_rows if a.cpt_issue and s.get(Job, a.job_id)}
    flags, rel = _analyze(jobs, st)
    near = geo.center(st.near_location) if st.near_location else None
    out = []
    for j in jobs:
        f = flags[j.id]
        m = rel.get(j.id) or {"relevance": 0.0, "lane": "?", "matched": [], "missing": [], "parts": {}}  # added since cache built
        r, lane = m["relevance"], m["lane"]
        boosted = _word(j.company.lower(), st.company_allow)
        posted = j.posted_at if j.posted_at.tzinfo else j.posted_at.replace(tzinfo=timezone.utc)
        age_days = (now - posted).total_seconds() / 86400
        h1b_n = h1b.approvals(j.company)
        issue = _key(j)[0] in cpt_issue
        friendly, friendly_why = _friendliness(f.cpt, h1b_n, issue, st)
        deadline = _deadline(j, posted)
        reasons = apply(j.title, j.company, j.location, j.description, posted, now, f, st,
                        j.id in applied_ids, _key(j)[0] in applied_companies)
        warnings = list(f.warnings)
        if apps.get(j.id) == "dismissed":
            reasons.append("not interested")
        if issue:
            (reasons if st.cpt_issue_companies == "hide" else warnings).append("rejected you over CPT before")
        if st.only_h1b_sponsors and (h1b_n or 0) < st.h1b_min_approvals:
            reasons.append("no H-1B history")
        if deadline and deadline < now and st.hide_expired:
            reasons.append("deadline passed")
        dist = None
        if near:
            dist = geo.nearest(j.location, near)
            remote_ok = st.near_include_remote and f.work_mode == "remote"
            if dist is None and not (remote_ok or st.near_keep_unknown):
                reasons.append("location unknown")
            elif dist is not None and dist > st.near_radius_miles and not remote_ok:
                reasons.append(f"{dist:.0f} mi from {near[2]}")
        if st.stale_days and age_days > st.stale_days:
            warnings.append(f"open {int(age_days)}+ days")
        out.append({
            "id": j.id, "source": j.source, "company": j.company, "title": j.title, "location": j.location, "url": j.url,
            "posted_at": posted, "first_seen": j.first_seen, "age_days": round(age_days, 1),
            "is_new": age_days * 24 < st.new_badge_hours, "deadline": deadline,
            "days_left": None if not deadline else round((deadline - now).total_seconds() / 86400, 1),
            "job_type": f.job_type, "cpt": f.cpt, "cpt_hits": f.cpt_hits, "cpt_label": friendly_why, "auth": f.auth,
            "space": f.space, "education": f.education, "categories": f.categories, "seasons": f.seasons,
            "work_mode": f.work_mode, "hourly": f.hourly, "warnings": warnings, "boosted": boosted,
            "has_description": bool(j.description), "h1b_approvals": h1b_n,
            "distance_mi": None if dist is None else round(dist), "near": near[2] if near else None,
            "best_lane": lane, "relevance": round(r, 2), "hidden_reasons": reasons, "status": apps.get(j.id),
            "skills_matched": m["matched"], "skills_missing": m["missing"], "relevance_parts": m["parts"],
            "_key": _key(j), "_fresh": ranking.freshness(posted, now, st.freshness_half_life_days), "_friendly": friendly,
        })
    learned, info = personal.predict(out, apps)
    w = st.weights.model_dump()
    for i, x in enumerate(out):
        p = None if learned is None else float(learned[i])
        x["personal"] = None if p is None else round(p, 2)
        x["score"], x["score_parts"] = ranking.score(x["relevance"], x["_fresh"], x["_friendly"], w,
                                                     st.allow_boost if x["boosted"] else 0, p)
        if x["score"] < st.min_score:
            x["hidden_reasons"].append(f"score < {st.min_score:g}")
        if x["relevance"] * 100 < st.min_relevance:
            x["hidden_reasons"].append(f"relevance < {st.min_relevance:g}%")
    _cache["personal"] = info
    # shown before hidden, then best score; keep one card per posting and list the other sources on it
    out.sort(key=lambda x: (bool(x["hidden_reasons"]), -x["score"]))
    seen: dict[tuple, dict] = {}
    deduped = []
    for x in out:
        k = x.pop("_key")
        del x["_fresh"], x["_friendly"]
        if k in seen:
            seen[k].setdefault("also", []).append({"id": x["id"], "source": x["source"], "url": x["url"], "location": x["location"]})
            continue
        seen[k] = x
        deduped.append(x)
    return deduped


def personal_info() -> dict:
    return _cache.get("personal") or {"active": False}


def _deadline(j: Job, posted: datetime) -> datetime | None:
    key = (j.id, hash(j.description))
    if key not in _deadlines:
        _deadlines[key] = parse_deadline(j.description, posted) if j.description else None
    return _deadlines[key]


_deadlines: dict[tuple, datetime | None] = {}


_memo: dict = {"key": None, "at": 0.0, "rows": None}


def build_cached(st: Settings, max_age: float = 60) -> list[dict]:
    """build(), reused for up to `max_age` seconds while settings, jobs and your tracker are unchanged."""
    import time
    with session() as s:
        apps = tuple(sorted((a.job_id, a.status, a.cpt_issue) for a in s.exec(select(Application)).all()))
    key = (st.model_dump_json(), _version(), hash(apps))
    if _memo["key"] == key and time.time() - _memo["at"] < max_age:
        return _memo["rows"]
    rows = build(st)
    _memo.update(key=key, at=time.time(), rows=rows)
    return rows
