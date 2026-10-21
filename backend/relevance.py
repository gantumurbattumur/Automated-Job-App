"""Resume <-> job relevance, all local: embeddings (fastembed, cached in SQLite) + skills overlap + title targets."""
import hashlib
import re
import threading
from functools import lru_cache

import numpy as np
from sqlmodel import select

from .config import ROOT, load
from .filters import _rx
from .models import Job, JobVector, session

MODEL = "BAAI/bge-small-en-v1.5"
_model_lock = threading.Lock()


@lru_cache
def _model():
    from fastembed import TextEmbedding  # slow import; only when first needed
    return TextEmbedding(MODEL, cache_dir=str(ROOT / "data" / "models"))


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    with _model_lock:
        return np.array(list(_model().embed(texts, batch_size=64)), dtype=np.float32)


# --- skills ---------------------------------------------------------------

@lru_cache
def skills_dict() -> dict[str, list[str]]:
    out = {}
    for line in (ROOT / "config" / "skills.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):  # whole-line comments only: "c#" is a skill
            names = [x.strip().lower() for x in line.split("|") if x.strip()]
            out[names[0]] = names
    return out


_skills_cache: dict[str, tuple[str, set[str]]] = {}  # job_id -> (text hash, skills)
_vec_cache: dict[str, tuple[str, np.ndarray]] = {}  # job_id -> (text hash, vector)


def job_skills(j: Job) -> set[str]:
    h = _hash(f"{j.title} {j.description}")
    hit = _skills_cache.get(j.id)
    if not hit or hit[0] != h:
        hit = _skills_cache[j.id] = (h, skills_in(f"{j.title} {j.description}"))
    return hit[1]


def skills_in(text: str) -> set[str]:
    t = text.lower()
    return {canon for canon, names in skills_dict().items() if _rx(tuple(names)).search(t)}


# --- resume lanes ---------------------------------------------------------

def _chunks(text: str) -> list[str]:
    """Resume split into small pieces (bullets / lines grouped) so one section can match a job strongly."""
    lines = [l.strip(" ●•-\t") for l in text.splitlines() if len(l.strip()) > 20]
    return [" ".join(lines[i:i + 3]) for i in range(0, len(lines), 3)] or [text]


@lru_cache
def lanes() -> dict[str, dict]:
    from . import resume
    cfg = load("resumes")
    out = {}
    for name, text in resume.lane_texts().items():
        out[name] = {"vecs": embed(_chunks(text)), "skills": skills_in(text),
                     "targets": [t.lower() for t in (cfg.get("targets") or {}).get(name, [])]}
    return out


# --- job vectors ------------------------------------------------------------

def job_text(j: Job) -> str:
    return f"{j.title}. {j.title}. {j.meta.get('category', '')}. {j.description[:1800]}"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def ensure_vectors(jobs: list[Job]) -> dict[str, np.ndarray]:
    """Vectors for `jobs`, computing (and storing) only those whose text changed."""
    texts = {j.id: job_text(j) for j in jobs}
    hashes = {jid: _hash(t) for jid, t in texts.items()}
    out = {jid: _vec_cache[jid][1] for jid in texts if jid in _vec_cache and _vec_cache[jid][0] == hashes[jid]}
    if len(out) < len(texts):  # first call (or new jobs): look in the database before embedding
        missing = [x for x in texts if x not in out]
        with session() as s:
            for i in range(0, len(missing), 900):  # stay under SQLite's bound-parameter limit
                for v in s.exec(select(JobVector).where(JobVector.job_id.in_(missing[i:i + 900]))).all():
                    if v.text_hash == hashes[v.job_id]:
                        out[v.job_id] = np.frombuffer(v.vec, dtype=np.float32)
                        _vec_cache[v.job_id] = (v.text_hash, out[v.job_id])
    todo = [jid for jid in texts if jid not in out]
    for i in range(0, len(todo), 512):
        batch = todo[i:i + 512]
        vecs = embed([texts[x] for x in batch])
        with session() as s:
            for jid, vec in zip(batch, vecs):
                row = s.get(JobVector, jid) or JobVector(job_id=jid)
                row.text_hash, row.vec = hashes[jid], vec.tobytes()
                s.add(row)
                out[jid] = vec
                _vec_cache[jid] = (hashes[jid], vec)
            s.commit()
    return out


def _percentile(x: np.ndarray) -> np.ndarray:
    """Rank-normalize to 0..1 so embedding scores mean "better than N% of open postings"."""
    if len(x) < 2:
        return np.ones_like(x)
    return x.argsort().argsort() / (len(x) - 1)


def score(jobs: list[Job], primary: str, margin: float, forced: str | None = None) -> dict[str, dict]:
    """{job_id: {relevance 0-1, lane, matched, missing, parts}}. Primary lane wins unless another beats it by `margin`."""
    if not jobs:
        return {}
    vecs = ensure_vectors(jobs)
    J = np.stack([vecs[j.id] for j in jobs])
    L = lanes()
    names = [forced] if forced else list(L)
    job_skill_sets = [job_skills(j) for j in jobs]
    per_lane = {}
    for n in names:
        sims = J @ L[n]["vecs"].T  # jobs x chunks
        top = np.sort(sims, axis=1)[:, -3:].mean(axis=1)  # best 3 resume chunks
        emb = _percentile(top)
        sk = np.array([len(js & L[n]["skills"]) / len(js) if js else 0.5 for js in job_skill_sets])
        tg = L[n]["targets"]
        title = np.array([1.0 if tg and _rx(tuple(tg)).search(j.title.lower()) else 0.0 for j in jobs]) if tg else np.full(len(jobs), 0.5)
        per_lane[n] = (0.6 * emb + 0.25 * sk + 0.15 * title, emb, sk, title)
    out = {}
    pi = names.index(primary) if primary in names else 0
    for i, j in enumerate(jobs):
        best = pi
        for k in range(len(names)):
            if k != pi and per_lane[names[k]][0][i] > per_lane[names[pi]][0][i] * (1 + margin):
                best = k
        n = names[best]
        total, emb, sk, title = (a[i] for a in per_lane[n])
        js = job_skill_sets[i]
        out[j.id] = {"relevance": float(total), "lane": n,
                     "matched": sorted(js & L[n]["skills"]), "missing": sorted(js - L[n]["skills"]),
                     "parts": {"semantic": round(float(emb), 2), "skills": round(float(sk), 2), "title": round(float(title), 2)}}
    return out


# --- resume help ------------------------------------------------------------

def _lines(text: str) -> list[str]:
    """Whole resume bullets: PDF text wraps a bullet over several lines, so join lines until the next bullet marker."""
    bullets, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if re.match(r"^[●•▪◦\-–]\s*", line):
            if cur:
                bullets.append(cur)
            cur = re.sub(r"^[●•▪◦\-–]\s*", "", line)
        elif cur is not None and line and not line.isupper() and " | " not in line and not re.search(r"(19|20)\d\d\s*[-–]\s*([A-Za-z]+\.?\s+)?((19|20)\d\d|current|present)", line, re.I):
            cur += " " + line
        else:
            if cur:
                bullets.append(cur)
            cur = None
    if cur:
        bullets.append(cur)
    return [b for b in bullets if len(b) >= 40]


@lru_cache
def _lane_lines() -> dict[str, tuple[list[str], np.ndarray]]:
    from . import resume
    return {name: (ls := _lines(text), embed(ls)) for name, text in resume.lane_texts().items()}


def resume_help(j: Job, n_bullets: int = 5) -> dict:
    """Which resume to send, which of your bullets to lead with, and which missing skills you actually have elsewhere."""
    vec = ensure_vectors([j])[j.id]
    L = lanes()
    sims = {name: float(np.sort(L[name]["vecs"] @ vec)[-3:].mean()) for name in L}
    lane = max(sims, key=sims.get)
    lines, lvecs = _lane_lines()[lane]
    order = np.argsort(lvecs @ vec)[::-1][:n_bullets] if len(lines) else []
    js = job_skills(j)
    have_any = set().union(*(L[n]["skills"] for n in L))
    missing = sorted(js - L[lane]["skills"])
    return {
        "lane": lane, "lane_similarity": {k: round(v, 3) for k, v in sorted(sims.items(), key=lambda t: -t[1])},
        "bullets": [{"text": lines[i], "similarity": round(float(lvecs[i] @ vec), 3)} for i in order],
        "matched": sorted(js & L[lane]["skills"]),
        "missing_but_on_other_resume": [s for s in missing if s in have_any],
        "missing": [s for s in missing if s not in have_any],
    }


def claude_prompt(j: Job, help: dict, resume_text: str) -> str:
    """Paste into Claude (your existing plan) for a tailored rewrite. Nothing is sent automatically."""
    skills = ", ".join(help["matched"] + help["missing_but_on_other_resume"]) or "(none detected)"
    return f"""Help me tailor my resume for this internship. Only use experience that is already in my resume — do not invent \
anything, and flag any job requirement I can't honestly claim.

JOB: {j.title} at {j.company} ({j.location})
{j.description[:5000]}

MY RESUME ({help['lane']}):
{resume_text[:6000]}

Please:
1. Pick the 3-5 bullets I should lead with and rewrite them to mirror this job's wording (same facts, same numbers).
2. Suggest a one-line summary for the top of the resume aimed at this role.
3. Write my Skills section ordered for this job. Skills it asks for that I have: {skills}.
4. List requirements I don't meet ({', '.join(help['missing']) or 'none detected'}) and whether any project of mine is close enough to mention.
5. Keep it to one page."""
