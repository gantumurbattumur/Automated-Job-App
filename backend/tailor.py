"""Resume tailoring: compare a job description with your resume and score the match the way ATS tools do.

Priorities follow Jobscan's published order — hard skills first (frequent ones weigh more), then education (when a
degree is required), job title, soft skills — plus a requirements check: is each requirement backed by a bullet?
Writing new bullets is left to Claude (your plan) via a generated prompt; the answer is pasted back and re-scored.
Nothing here produces resume files: suggestions are text you review and copy yourself.
"""
import json
import re
from functools import lru_cache

import numpy as np

from . import relevance
from .config import ROOT

WEIGHTS = {"hard_skills": 0.40, "requirements": 0.25, "title": 0.15, "education": 0.10, "soft_skills": 0.10}
SECTION_FACTOR = {"required": 1.5, "responsibilities": 1.2, "preferred": 1.0, "other": 0.6}
BULLET = re.compile(r"^\s*[●•▪◦\-–*]\s+")

# --- resume -----------------------------------------------------------------

RESUME_SECTIONS = {"summary": r"summary|profile|objective|about", "skills": r"skills|technical skills|technologies",
                   "experience": r"experience|work experience|employment|professional experience",
                   "projects": r"projects|selected projects|personal projects", "education": r"education",
                   "other": r"awards|certifications|publications|leadership|activities|volunteer|research"}


def _section_of(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 40 or not re.search(r"[A-Za-z]", s) or (s != s.upper() and not s.endswith(":")):
        return None
    for name, pat in RESUME_SECTIONS.items():
        if re.fullmatch(rf"(?:{pat})\s*:?", s, re.I):
            return name
    return None


DATE_RANGE = re.compile(r"(?:19|20)\d\d\s*[-–]\s*(?:[A-Za-z]+\.?\s+)?(?:(?:19|20)\d\d|current|present)", re.I)


def _looks_like_header(line: str) -> bool:
    """A job/project title line: has dates, a "|" separator, a location, or is a short Title Case name."""
    words = line.split()
    return bool(DATE_RANGE.search(line) or " | " in line or re.search(r"github|link\b", line, re.I)
                or re.search(r",\s*[A-Z]{2}$", line) or (len(words) <= 6 and all(w[:1].isupper() or not w[:1].isalpha() for w in words)))


def parse_resume(text: str) -> dict:
    """{summary, skills: [lines], entries: [{id, section, header, bullets: [{id, text}]}], education}"""
    out = {"summary": "", "skills": [], "entries": [], "education": "", "header": ""}
    section, entry, bullet = "header", None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if (sec := _section_of(line)):
            section, entry, bullet = sec, None, None
            continue
        if section in ("experience", "projects", "other"):
            if BULLET.match(line):
                entry = entry or {"section": section, "header": "", "bullets": []}
                if entry not in out["entries"]:
                    out["entries"].append(entry)
                bullet = BULLET.sub("", line)
                entry["bullets"].append(bullet)
            elif bullet is not None and not _looks_like_header(line) and not bullet.rstrip().endswith((".", "!", "?")):
                # wrapped continuation of the bullet above
                entry["bullets"][-1] += " " + line
                bullet = entry["bullets"][-1]
            else:  # a new job / project header line (or its 2nd line: company, location)
                if entry is None or entry["bullets"]:
                    entry = {"section": section, "header": line, "bullets": []}
                    out["entries"].append(entry)
                else:
                    entry["header"] += " · " + line
                bullet = None
        elif section == "summary":
            out["summary"] += (" " if out["summary"] else "") + line
        elif section == "skills":
            out["skills"].append(line)
        elif section == "education":
            out["education"] += (" " if out["education"] else "") + line
        else:
            out["header"] += (" " if out["header"] else "") + line
    for i, e in enumerate(out["entries"], 1):  # stable ids for the prompt: E1.2 = 2nd bullet of the 1st entry
        e["id"] = f"E{i}"
        e["bullets"] = [{"id": f"E{i}.{j}", "text": b} for j, b in enumerate(e["bullets"], 1)]
    return out


def resume_text(r: dict) -> str:
    parts = [r["header"], "SUMMARY", r["summary"], "SKILLS", *r["skills"]]
    for e in r["entries"]:
        parts += [e["header"], *("● " + b["text"] for b in e["bullets"])]
    parts += ["EDUCATION", r["education"]]
    return "\n".join(p for p in parts if p)


# --- job description ----------------------------------------------------------

JD_HEADERS = [
    ("required", r"minimum qualifications|basic qualifications|required qualifications|education (?:&|and) experience(?: required)?|"
                 r"skills (?:&|and) experience|what you(?:'|’)ll bring|what you bring|requirements|what you(?:'|’)ll need|"
                 r"what we(?:'|’)re looking for|who you are|you (?:have|bring|should have)|must[- ]haves?|qualifications"),
    ("preferred", r"preferred qualifications|preferred skills|nice[- ]to[- ]haves?|bonus points|pluses|it(?:'|’)s a plus|"
                  r"desired qualifications|additional qualifications"),
    ("responsibilities", r"responsibilities|what you(?:'|’)ll do|what you will do|your role|the role|in this role|day[- ]to[- ]day|job description|"
                         r"you will|key duties|position overview"),
    ("other", r"benefits|perks|compensation|pay range|salary|about us|about the company|equal opportunity|eeo|what we (?:can )?offer(?: you)?|"
              r"accommodations?|our vision|who we are"),
]
@lru_cache
def soft_skills() -> dict[str, list[str]]:
    out = {}
    for line in (ROOT / "config" / "soft_skills.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names = [x.strip().lower() for x in line.split("|") if x.strip()]
            out[names[0]] = names
    return out


def _split_sections(text: str) -> dict[str, str]:
    marks = []
    for name, pat in JD_HEADERS:
        for m in re.finditer(rf"(?i)(?:^|[\n.:!?•]\s*|\s)({pat})\b\s*[:\-–]?", text):
            marks.append((m.start(1), m.end(), name))
    marks.sort()
    kept, last_end = [], -1
    for s, e, name in marks:  # drop overlapping matches ("qualifications" inside "minimum qualifications")
        if s >= last_end:
            kept.append((s, e, name))
            last_end = e
    out = {"intro": text[: kept[0][0]] if kept else text}
    for i, (s, e, name) in enumerate(kept):
        end = kept[i + 1][0] if i + 1 < len(kept) else len(text)
        out[name] = (out.get(name, "") + " " + text[e:end]).strip()
    return out


BOILERPLATE = re.compile(r"equal opportunity|affirmative action|benefits|medical|dental|paid time off|pto\b|401\(?k|salary|pay range|"
                         r"compensation|fraud|recruitment fees|accommodation|e-verify|privacy|world leader|alert\b|"
                         r"discriminat|veteran status|sexual orientation|background check|visa|sponsorship|apply now|disqualif|"
                         r"misrepresent|ai tools to enhance|accessib|inclusive|committed to (creating|providing|diversity)|"
                         r"if you believe you require|reasonable accommodation|knowledge expert in your field", re.I)


# a requirement asks for a skill, experience or task; culture lines ("We make bold moves") don't
REQUIREMENT_SIGNAL = re.compile(r"experience|knowledge|familiar|proficien|abilit|skill|degree|pursuing|enrolled|understand|"
                                r"build|develop|design|implement|create|work (with|on)|using|strong|must|required|preferred|"
                                r"plus|years|background in|expertise|hands-on|support|analy|collaborat|communicat|manage|"
                                r"maintain|test|deploy|write|research|learn|model|data|software|code|program", re.I)
CULTURE = re.compile(r"follow @|instagram|linkedin\.com|#\w+|we (know|value|make|believe|are proud|celebrate)|our culture|"
                     r"flexibility to manage|life at|join us|why you(?:'|’)ll love|days (?:a|per) week|from an? [\w&]+ office", re.I)
# list items run together without punctuation ("...optimization Design, implement..."): a new item starts with one of these
ITEM_START = re.compile(r"(?<=[a-z0-9)%])\s+(?=(?:Collaborat|Work|Design|Guide|Use|Identif|Develop|Deploy|Contribut|Analy|Build|"
                        r"Implement|Create|Familiar|Proficien|A basic|Strong|Great|Relevant|Experience|Knowledge|Abilit|Working|"
                        r"Pursuing|Currently|Excellent|Understand|Demonstrat|Must|Prior|Comfortable|Hands-on|"
                        r"Solid|Good|Partner|Own|Lead|Drive|Support|Maintain|Write|Test|Research|Explore|Communicat)\w*\b)")


def _items(segment: str) -> list[str]:
    """Requirement-sized pieces: bullets / sentences, skipping fragments, boilerplate and culture copy."""
    parts = [q for p in re.split(r"(?<=[.!?;])\s+(?=[A-Z])|\s*[•·▪]\s*|\n+", segment) for q in ITEM_START.split(p)]
    return [p.strip(" -–:;") for p in parts if 25 <= len(p.strip()) <= 320 and not BOILERPLATE.search(p)
            and not CULTURE.search(p) and REQUIREMENT_SIGNAL.search(p)]


LIST_INTRO = re.compile(r"(?:such as|including|e\.g\.,?|like|for example|experience with|familiarity with|knowledge of|"
                        r"proficiency in|proficient in)\s+([^.;:]{3,200})", re.I)
TECH_TOKEN = re.compile(r"(?<![\w-])(?:[A-Z][a-z]+[A-Z]\w*|[A-Za-z]+(?:\.js|#|\+\+)|[A-Z]{2,6}s?|\.NET)(?![\w-])")
NOT_KEYWORDS = {"us", "usa", "eeo", "eoe", "hr", "ceo", "llc", "inc", "the", "and", "or", "it", "pto", "ada", "u.s", "id", "ii",
                "iii", "ok", "faq", "new", "team", "you", "we", "our", "all", "etc", "ai/ml", "fte", "gpa", "nyc", "sf", "la"}


def _list_terms(text: str) -> list[str]:
    terms = []
    for m in LIST_INTRO.finditer(text):
        for t in re.split(r",|\bor\b|\band\b|/|\(|\)", m[1]):
            t = t.strip(" .")
            names_like = all(w[:1].isupper() or re.search(r"[#+.]", w) for w in t.split())  # "Microsoft SQL Server", not "AI orchestration platforms"
            if 2 <= len(t) <= 30 and len(t.split()) <= 3 and names_like and t.lower() not in NOT_KEYWORDS:
                terms.append(t)
    return terms


def parse_jd(title: str, text: str) -> dict:
    secs = _split_sections(text or "")
    body = {k: v for k, v in secs.items() if k != "other"}
    weights: dict[str, dict] = {}

    def add(term: str, canon: str, where: str, n: int = 1):
        k = canon.lower()
        k = term.lower() if canon == term and k not in relevance.skills_dict() else k
        w = weights.setdefault(k, {"term": canon, "key": k, "count": 0, "weight": 0.0, "where": set()})
        w["count"] += n
        w["where"].add(where)

    for where, seg in body.items():
        where = where if where in SECTION_FACTOR else "other"
        for canon, names in relevance.skills_dict().items():
            hits = [m for a in names for m in re.finditer(rf"(?i)(?<!\w){re.escape(a)}(?!\w)", seg)]
            if hits:  # show it the way the job writes it ("Kubernetes", "CI/CD"), match it by the dictionary
                add(canon, hits[0].group(0), where, len(hits))
        rest = seg  # blank out dictionary skills so "CI/CD" doesn't also yield "CI" and "CD"
        for names in relevance.skills_dict().values():
            for a in names:
                rest = re.sub(rf"(?i)(?<!\w){re.escape(a)}(?!\w)", " ", rest)
        for t in _list_terms(rest) + TECH_TOKEN.findall(rest):
            dotted = {a.split(".")[0] for names in relevance.skills_dict().values() for a in names if "." in a}  # "Node" of "Node.js"
            if t.lower() not in NOT_KEYWORDS and t.lower() not in dotted and not any(t.lower() in names for names in relevance.skills_dict().values()):
                add(t, t, where)
    for w in weights.values():
        w["weight"] = round(min(w["count"], 3) * max(SECTION_FACTOR[x if x in SECTION_FACTOR else "other"] for x in w["where"]), 2)
        w["where"] = sorted(w["where"])
    keywords = sorted(weights.values(), key=lambda w: -w["weight"])

    alltext = " ".join(body.values()).lower()
    soft = [canon for canon, names in soft_skills().items() if any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)", alltext) for a in names)]
    edu_level = ("phd" if re.search(r"\bph\.?d\b|doctoral", alltext) and not re.search(r"bachelor|master|b\.?s\.?\b|m\.?s\.?\b", alltext)
                 else "master" if re.search(r"master(?:'|’)?s? degree|\bm\.?s\.? (?:degree|in)\b|graduate degree", alltext)
                 and not re.search(r"bachelor", alltext) else "bachelor" if re.search(r"bachelor|\bb\.?s\.?\b|undergraduate|degree in", alltext) else None)
    fields = [f for f in ("computer science", "computer engineering", "software engineering", "data science", "statistics",
                          "mathematics", "electrical engineering", "information technology", "information systems", "physics")
              if f in alltext]
    years = re.search(r"(\d+)\+?\s*(?:-\s*\d+\s*)?years?(?: of)? (?:relevant |professional |industry )?experience", alltext)
    items = []
    for where in ("required", "responsibilities", "preferred"):
        items += [{"text": t, "where": where} for t in _items(secs.get(where, ""))]
    if not items:  # no recognizable headers: use the whole description's sentences
        items = [{"text": t, "where": "other"} for t in _items(secs.get("intro", ""))][:25]
    return {"title": title, "title_core": core_title(title), "keywords": keywords, "soft_skills": soft,
            "education": {"level": edu_level, "fields": fields}, "years": int(years[1]) if years else None,
            "requirements": items[:40], "sections_found": sorted(k for k in secs if k != "intro")}


def core_title(title: str) -> str:
    t = re.sub(r"\([^)]*\)|\b(19|20)\d\d\b|\b(summer|fall|spring|winter|intern(ship)?s?|co-?op|new grad|student|early career|"
               r"graduate|undergraduate|phd|mba|ms|bs)\b", " ", title.lower())
    t = re.split(r"\s[-–|,]\s", t)[0]
    return re.sub(r"[^a-z0-9+#/ ]+", " ", re.sub(r"\s+", " ", t)).strip()


# --- matching ---------------------------------------------------------------

_vec_cache: dict[str, np.ndarray] = {}


def _embed(texts: list[str]) -> np.ndarray:
    todo = [t for t in dict.fromkeys(texts) if t not in _vec_cache]
    if todo:
        for t, v in zip(todo, relevance.embed(todo)):
            _vec_cache[t] = v
    return np.stack([_vec_cache[t] for t in texts]) if texts else np.zeros((0, 384), dtype=np.float32)


def _soft_re(alias: str) -> str:
    if " " in alias or "-" in alias:
        return rf"(?<!\w){re.escape(alias)}(?!\w)"
    stem = re.sub(r"(e|ion|ing|ive|ed)$", "", alias)
    return rf"(?<!\w){re.escape(stem if len(stem) >= 4 else alias)}\w*"


def _soft_in(skill: str, text_low: str) -> bool:
    return any(re.search(_soft_re(a), text_low) for a in soft_skills()[skill])


def _present(term: str, text_low: str) -> bool:
    names = relevance.skills_dict().get(term.lower()) or next(
        (n for n in relevance.skills_dict().values() if term.lower() in n), [term.lower()])
    return any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text_low) for a in names)


MET, PARTIAL = 0.70, 0.62  # bge-small cosine between a requirement and your best bullet (calibrated on real JDs)


def analyze(jd: dict, resume: dict, other_resume_text: str = "") -> dict:
    text = resume_text(resume)
    low = text.lower()
    other_low = other_resume_text.lower()
    # hard skills: frequency- and section-weighted keyword coverage
    kws = []
    for k in jd["keywords"]:
        have = _present(k["term"], low)
        kws.append({**k, "in_resume": have, "on_other_resume": (not have) and bool(other_low) and _present(k["term"], other_low)})
    total_w = sum(k["weight"] for k in kws) or 1
    hard = sum(k["weight"] for k in kws if k["in_resume"]) / total_w if kws else 1.0
    # job title: the role words ("software engineer", "machine learning") should appear in your resume
    tokens = [t for t in jd["title_core"].split() if len(t) > 1 and t not in {"and", "of", "the", "for"}]
    # "ai/ml" counts if the resume shows either part
    title_hit = [t for t in tokens if any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", low) for p in t.split("/") if p)]
    phrase = bool(jd["title_core"]) and jd["title_core"] in low
    title = 1.0 if phrase or not tokens else len(title_hit) / len(tokens) * 0.8
    # education
    edu = jd["education"]
    level_ok = {"bachelor": r"bachelor|b\.?s\.?\b|master|m\.?s\.?\b", "master": r"master|m\.?s\.?\b", "phd": r"ph\.?d"}
    if not edu["level"] and not edu["fields"]:
        education = 1.0
    else:
        lv = 1.0 if not edu["level"] or re.search(level_ok[edu["level"]], low) else 0.0
        fd = 1.0 if not edu["fields"] or any(f in low for f in edu["fields"]) else 0.0
        education = 0.5 * lv + 0.5 * fd
    # soft skills
    soft = [{"skill": s, "in_resume": _soft_in(s, low)} for s in jd["soft_skills"]]
    soft_score = sum(s["in_resume"] for s in soft) / len(soft) if soft else 1.0
    # requirements: best supporting bullet for each
    bullets = [(e, b) for e in resume["entries"] for b in e["bullets"]]
    reqs = []
    if jd["requirements"]:  # with no bullets at all, every requirement is unsupported (not "nothing to check")
        R = _embed([r["text"] for r in jd["requirements"]])
        S = R @ _embed([b["text"] for _, b in bullets]).T if bullets else None
        for i, r in enumerate(jd["requirements"]):
            j = int(S[i].argmax()) if bullets else -1
            sim = float(S[i, j]) if bullets else 0.0
            req_terms = [k["term"] for k in kws if _present(k["term"], r["text"].lower())]
            covered = [t for t in req_terms if _present(t, low)]
            status = "met" if sim >= MET or (req_terms and len(covered) == len(req_terms) and sim >= PARTIAL) else \
                "partial" if sim >= PARTIAL or covered else "missing"
            t = r["text"].lower()
            kind = "bullet"
            if re.search(r"degree|bachelor|master|ph\.?d|pursuing|enrolled", t):
                kind, status = "education", "met" if education >= 1 else "partial" if education > 0 else "missing"
            elif re.search(r"\d+\+?\s*(?:-\s*\d+\s*)?years?", t):
                n = int(re.search(r"(\d+)", t)[1])
                kind, status = "years", "met" if n <= 2 and resume["entries"] else "partial"
            else:
                soft_here = [sk for sk, names in soft_skills().items() if any(re.search(_soft_re(a), t) for a in names)]
                if soft_here and not req_terms and (len(soft_here) >= 2 or len(t) < 110):  # mostly about soft skills
                    kind = "soft"
                    got = sum(_soft_in(sk, low) for sk in soft_here)
                    status = "met" if got == len(soft_here) else "partial" if got else "missing"
            if status == "met" and kind == "bullet" and len(covered) < len(req_terms):
                status = "partial"  # the bullet is close, but the job's own terms (e.g. "MCP") aren't in your resume
            e, b = bullets[j] if bullets else ({"header": ""}, {"id": "—", "text": "no bullets in this resume"})
            reqs.append({**r, "kind": kind, "status": status, "similarity": round(sim, 3), "evidence": {"id": b["id"], "text": b["text"], "entry": e["header"]},
                         "terms": req_terms, "missing_terms": [t for t in req_terms if t not in covered]})
    rw = {"met": 1.0, "partial": 0.5, "missing": 0.0}
    req_total = sum(SECTION_FACTOR.get(r["where"], 0.6) for r in reqs) or 1
    req_score = sum(rw[r["status"]] * SECTION_FACTOR.get(r["where"], 0.6) for r in reqs) / req_total if reqs else 1.0
    parts = {"hard_skills": hard, "requirements": req_score, "title": title, "education": education, "soft_skills": soft_score}
    score = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
    return {"score": score, "parts": {k: round(v * 100) for k, v in parts.items()}, "keywords": kws,
            "title": {"core": jd["title_core"], "hits": title_hit, "missing": [t for t in tokens if t not in title_hit], "phrase": phrase},
            "education": {**edu, "score": round(education * 100)}, "soft_skills": soft, "requirements": reqs, "years": jd["years"]}


# --- suggestions (written by Claude, pasted back) --------------------------------

def build_prompt(job: dict, jd: dict, resume: dict, a: dict) -> str:
    missing = [k for k in a["keywords"] if not k["in_resume"]][:25]
    weak = [r for r in a["requirements"] if r["status"] != "met"][:15]
    lines = []
    for e in resume["entries"]:
        lines.append(f"[{e['id']}] {e['section'].upper()}: {e['header']}")
        lines += [f"  {b['id']}: {b['text']}" for b in e["bullets"]]
    return f"""You are helping me tailor my resume to one job. My goal is a 90-95% keyword/requirement match, but ONLY with
truthful content. Never invent employers, projects, tools, metrics, or results that are not supported by my resume.
If a requirement can't be met honestly, list it under "gaps" instead of writing a bullet for it.

JOB: {job.get('title', '')} at {job.get('company', '')}
Current match: {a['score']}% (hard skills {a['parts']['hard_skills']}%, requirements {a['parts']['requirements']}%, title {a['parts']['title']}%,
education {a['parts']['education']}%, soft skills {a['parts']['soft_skills']}%)

KEYWORDS THE JOB USES THAT MY RESUME LACKS (most important first; use the exact wording when it's true for me):
{chr(10).join(f"- {k['term']} (weight {k['weight']}{', on my other resume' if k['on_other_resume'] else ''})" for k in missing) or '- none'}

REQUIREMENTS NOT CLEARLY SHOWN (status / closest bullet I have):
{chr(10).join(f"- [{r['status']}] {r['text'][:220]}  (closest: {r['evidence']['id']})" for r in weak) or '- none'}

Job title words to reflect if accurate: {', '.join(a['title']['missing']) or 'already covered'}

MY RESUME BULLETS (ids are stable; refer to them):
{chr(10).join(lines)}

MY SKILLS SECTION:
{chr(10).join(resume['skills'])}

Rewrite or add bullets so the resume shows the job's keywords and requirements where my experience supports them.
Rules: start with a strong action verb; keep my real numbers; one line (<= 30 words); no buzzword stuffing; at most
8 suggestions; prefer rewriting existing bullets over adding new ones.

Reply with JSON only, exactly in this shape:
{{"suggestions": [
  {{"type": "rewrite", "target": "E1.2", "text": "...", "keywords": ["..."], "why": "which requirement it covers"}},
  {{"type": "new", "target": "E3", "text": "...", "keywords": ["..."], "why": "..."}}
 ],
 "skills_to_add": ["only skills I really have"],
 "summary": "optional 1-2 sentence summary aimed at this role",
 "gaps": ["requirements I can't honestly claim"]}}
"""


def parse_suggestions(raw: str) -> dict:
    """Claude's JSON (tolerates ```json fences and text around it); falls back to one suggestion per bullet line."""
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            d = json.loads(m[0])
            sug = []
            for i, s in enumerate(d.get("suggestions") or [], 1):
                if isinstance(s, dict) and str(s.get("text", "")).strip():
                    sug.append({"id": f"s{i}", "type": "rewrite" if s.get("type") == "rewrite" else "new",
                                "target": str(s.get("target") or ""), "text": str(s["text"]).strip(),
                                "keywords": [str(k) for k in s.get("keywords") or []], "why": str(s.get("why") or ""),
                                "status": "pending"})
            return {"suggestions": sug, "skills_to_add": [str(x) for x in d.get("skills_to_add") or []],
                    "summary": str(d.get("summary") or ""), "gaps": [str(x) for x in d.get("gaps") or []]}
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    lines = [BULLET.sub("", l).strip() for l in raw.splitlines() if BULLET.match(l) and len(l.strip()) > 20]
    return {"suggestions": [{"id": f"s{i}", "type": "new", "target": "", "text": t, "keywords": [], "why": "", "status": "pending"}
                            for i, t in enumerate(lines, 1)], "skills_to_add": [], "summary": "", "gaps": []}


def apply_suggestions(resume: dict, data: dict, statuses: tuple[str, ...] = ("accepted",)) -> dict:
    """Your resume as it would read with these suggestions applied (for re-scoring only — never written to a file)."""
    r = json.loads(json.dumps(resume))
    by_id = {b["id"]: b for e in r["entries"] for b in e["bullets"]}
    entries = {e["id"]: e for e in r["entries"]}
    for s in data.get("suggestions", []):
        if s["status"] not in statuses:
            continue
        if s["type"] == "rewrite" and s["target"] in by_id:
            by_id[s["target"]]["text"] = s["text"]
        else:
            target = entries.get(s["target"].split(".")[0]) or (r["entries"][-1] if r["entries"] else None)
            if target is None:
                target = {"id": "E99", "section": "projects", "header": "", "bullets": []}
                r["entries"].append(target)
            target["bullets"].append({"id": f"{target['id']}.new{len(target['bullets']) + 1}", "text": s["text"]})
    if data.get("skills_to_add") and data.get("skills_status", "pending") in statuses:
        r["skills"].append("Additional: " + ", ".join(data["skills_to_add"]))
    if data.get("summary") and data.get("summary_status", "pending") in statuses:
        r["summary"] = data["summary"]
    return r


def export_text(job: dict, resume: dict, data: dict) -> str:
    by_id = {b["id"]: (e, b) for e in resume["entries"] for b in e["bullets"]}
    entries = {e["id"]: e for e in resume["entries"]}
    out = [f"Tailored for: {job.get('title', '')} — {job.get('company', '')}", ""]
    if data.get("summary") and data.get("summary_status") == "accepted":
        out += ["SUMMARY", data["summary"], ""]
    groups: dict[str, list[str]] = {}
    for s in data.get("suggestions", []):
        if s["status"] != "accepted":
            continue
        if s["type"] == "rewrite" and s["target"] in by_id:
            e, b = by_id[s["target"]]
            groups.setdefault(e["header"], []).append(f"REPLACE: {b['text']}\n   WITH: {s['text']}")
        else:
            e = entries.get(s["target"].split(".")[0])
            groups.setdefault(e["header"] if e else "New bullets", []).append(f"ADD: {s['text']}")
    for header, items in groups.items():
        out += [header, *[f" • {i}" for i in items], ""]
    if data.get("skills_to_add") and data.get("skills_status") == "accepted":
        out += ["SKILLS — add: " + ", ".join(data["skills_to_add"]), ""]
    return "\n".join(out).strip() + "\n"


# --- tailoring list (up to 10 jobs) ---------------------------------------------

MAX_ITEMS = 10


def _models():
    from .models import Job, TailorItem, session
    return Job, TailorItem, session


def items() -> list:
    _, TailorItem, session = _models()
    from sqlmodel import select
    with session() as s:
        return sorted(s.exec(select(TailorItem)).all(), key=lambda t: t.added_at)


def add(job_id: str | None = None, company: str = "", title: str = "", jd_text: str = "", url: str = "") -> str:
    Job, TailorItem, session = _models()
    with session() as s:
        if len(items()) >= MAX_ITEMS:
            raise ValueError(f"You can tailor up to {MAX_ITEMS} jobs at a time — remove one first")
        if job_id:
            j = s.get(Job, job_id)
            if not j:
                raise KeyError(job_id)
            if s.get(TailorItem, job_id):
                return job_id
            from .feed import _key  # same posting listed by two sources (Simplify + Workday) -> one tailoring entry
            for t in items():
                other = s.get(Job, t.job_id) if t.job_id else None
                if other and _key(other) == _key(j):
                    return t.id
            if len(j.description or "") < 200:
                raise ValueError("JobPrep only has this posting's title — open it, copy the description, and paste it in instead")
            item = TailorItem(id=job_id, job_id=job_id, company=j.company, title=j.title, url=j.url, jd_text=j.description)
        else:
            if len(jd_text.strip()) < 200:
                raise ValueError("Paste the full job description (responsibilities and qualifications)")
            n = 1 + max([int(t.id.split(":")[1]) for t in items() if t.id.startswith("paste:")] or [0])
            item = TailorItem(id=f"paste:{n}", company=company.strip() or "Company", title=title.strip() or "Role",
                              url=url.strip(), jd_text=jd_text.strip())
        s.add(item)
        s.commit()
        return item.id


def get(item_id: str):
    _, TailorItem, session = _models()
    with session() as s:
        return s.get(TailorItem, item_id)


def save(item) -> None:
    _, _, session = _models()
    with session() as s:
        s.merge(item)
        s.commit()


def remove(item_id: str) -> None:
    _, TailorItem, session = _models()
    with session() as s:
        if (t := s.get(TailorItem, item_id)):
            s.delete(t)
            s.commit()


def _lane_for(item) -> str:
    from . import feed, resume, settings
    if item.lane in resume.names():
        return item.lane
    row = next((r for r in feed.build_cached(settings.get()) if r["id"] == item.job_id), {}) if item.job_id else {}
    if row.get("best_lane") in resume.names():
        return row["best_lane"]
    # pasted description: whichever resume reads closest
    texts = resume.lane_texts()
    if len(texts) > 1 and item.jd_text:
        v = _embed([item.jd_text[:2000]])[0]
        return max(texts, key=lambda n: float((_embed([texts[n][:2000]])[0] @ v)))
    return resume.primary()


def report(item_id: str) -> dict:
    """Everything the Tailor page shows for one job: analysis now, with accepted suggestions, and with all of them."""
    from . import resume
    item = get(item_id)
    if not item:
        raise KeyError(item_id)
    lane = _lane_for(item)
    base = parse_resume(resume.text(lane))
    other = " ".join(t for n, t in resume.lane_texts().items() if n != lane)
    jd = parse_jd(item.title, item.jd_text)
    now = analyze(jd, base, other)
    if item.baseline is None:
        item.baseline = now["score"]
        save(item)
    data = item.data or {}
    accepted = analyze(jd, apply_suggestions(base, data, ("accepted",)), other) if data.get("suggestions") else None
    everything = analyze(jd, apply_suggestions(base, data, ("accepted", "pending")), other) if data.get("suggestions") else None
    by_id = {b["id"]: b["text"] for e in base["entries"] for b in e["bullets"]}
    base_low = resume_text(base).lower()
    for sug in data.get("suggestions", []):  # what each suggestion would add — or take away — on its own
        sug["original"] = by_id.get(sug["target"], "") if sug["type"] == "rewrite" else ""
        sug["adds"] = [k["term"] for k in now["keywords"] if not k["in_resume"] and _present(k["term"], sug["text"].lower())]
        sug["removes"] = []
        if sug["original"]:
            after = base_low.replace(sug["original"].lower(), sug["text"].lower())
            lost = [k["term"] for k in now["keywords"] if k["in_resume"] and not _present(k["term"], after)]
            lost += [x["skill"] for x in now["soft_skills"] if x["in_resume"] and not _soft_in(x["skill"], after)]
            sug["removes"] = lost
    job = {"title": item.title, "company": item.company, "url": item.url}
    return {"id": item.id, "job": job, "job_id": item.job_id, "lane": lane, "lanes": resume.names(),
            "lane_labels": {n: l.get("label") or n for n, l in resume.config()["lanes"].items()},
            "baseline": item.baseline, "now": now, "with_accepted": accepted, "with_all": everything,
            "data": data, "resume": base, "prompt_ready": True, "sections_found": jd["sections_found"]}


def prompt(item_id: str) -> str:
    rep = report(item_id)
    item = get(item_id)
    jd = parse_jd(item.title, item.jd_text)
    return build_prompt(rep["job"], jd, rep["resume"], rep["now"])


def import_suggestions(item_id: str, raw: str) -> dict:
    item = get(item_id)
    if not item:
        raise KeyError(item_id)
    parsed = parse_suggestions(raw)
    if not parsed["suggestions"] and not parsed["skills_to_add"] and not parsed["summary"]:
        raise ValueError("Couldn't find any suggestions in that text — paste Claude's whole JSON reply")
    item.data = {**parsed, "skills_status": "pending", "summary_status": "pending", "raw": raw[:20000]}
    save(item)
    return report(item_id)


def decide(item_id: str, sug_id: str, status: str, text: str | None = None) -> dict:
    if status not in ("pending", "accepted", "rejected"):
        raise ValueError("status must be pending, accepted or rejected")
    item = get(item_id)
    if not item:
        raise KeyError(item_id)
    data = json.loads(json.dumps(item.data or {}))
    if sug_id in ("skills", "summary"):
        data[f"{sug_id}_status"] = status
    else:
        for sug in data.get("suggestions", []):
            if sug["id"] == sug_id:
                sug["status"] = status
                if text is not None and text.strip():
                    sug["text"] = text.strip()
                break
        else:
            raise KeyError(sug_id)
    item.data = data
    save(item)
    return report(item_id)


def export(item_id: str) -> str:
    rep = report(item_id)
    return export_text(rep["job"], rep["resume"], rep["data"])
