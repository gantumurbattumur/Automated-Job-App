"""Your personal info + canned application answers (data/profile.yaml, gitignored), and the per-job packet."""
import re

import yaml
from pydantic import BaseModel, ConfigDict

from .config import DATA, load
from .filters import _rx

PATH = DATA / "profile.yaml"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    answer: str
    patterns: list[str] = []  # phrases that identify this question on a form (lowercase, any of)
    note: str = ""


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = ""
    last_name: str = ""
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""  # "City, ST"
    linkedin: str = ""
    github: str = ""
    website: str = ""
    school: str = ""
    degree: str = ""
    major: str = ""
    graduation: str = ""  # e.g. "May 2028"
    gpa: str = ""
    undergrad_school: str = ""
    undergrad_degree: str = ""
    work_authorization: str = "F-1 student; authorized for internships via CPT (no employer sponsorship needed for CPT)"
    availability: str = "Summer 2027"
    worked_at: list[str] = []  # employers you've worked for -> "worked here before?" = Yes
    answers: list[Answer] = []


DEFAULT_ANSWERS = [
    Answer(question="Are you legally authorized to work in the United States?", answer="Yes",
           patterns=["legally authorized to work", "authorized to work in the united states", "eligible to work in the"],
           note="F-1 with CPT authorization for the internship"),
    Answer(question="Will you now or in the future require sponsorship for employment visa status (e.g. H-1B)?", answer="Yes",
           patterns=["now or in the future require", "future require sponsorship", "require sponsorship now or in the future",
                     "will you now or in the future", "sponsorship now or in the future", "now or in the future",
                     "in the future require", "require sponsorship in the future"],
           note="Honest default: CPT/OPT need no sponsorship, but a later H-1B would. Answering No could put an offer at risk."),
    Answer(question="Do you currently require visa sponsorship to work in this role?", answer="No",
           patterns=["currently require sponsorship", "require sponsorship to work in this role", "need sponsorship for this internship",
                     "require visa sponsorship"],
           note="CPT is work authorization through your school; the employer sponsors nothing for the internship"),
    Answer(question="Have you ever worked for {company}?", answer="No",
           patterns=["previously worked for", "ever worked for", "ever been employed by", "previously employed by",
                     "worked at {company}", "former employee"],
           note="Automatically Yes for employers listed in 'Companies I worked at'"),
    Answer(question="Are you at least 18 years old?", answer="Yes", patterns=["18 years", "at least 18"]),
    Answer(question="Are you currently enrolled in a degree program?", answer="Yes",
           patterns=["currently enrolled", "currently pursuing", "enrolled in a degree"]),
    Answer(question="Will you return to school after the internship?", answer="Yes", patterns=["return to school", "returning to school"]),
    Answer(question="Are you willing to relocate?", answer="Yes", patterns=["willing to relocate", "open to relocation"]),
    Answer(question="Are you able to work on-site / hybrid?", answer="Yes", patterns=["work on-site", "work onsite", "in-office", "hybrid"]),
    Answer(question="How did you hear about this position?", answer="Online job board",
           patterns=["how did you hear", "how did you learn about", "where did you hear"]),
    Answer(question="Gender", answer="Decline to self-identify", patterns=["gender"]),
    Answer(question="Race / ethnicity", answer="Decline to self-identify", patterns=["race", "ethnicity"]),
    Answer(question="Hispanic or Latino?", answer="Decline to self-identify", patterns=["hispanic", "latino"]),
    Answer(question="Veteran status", answer="I am not a protected veteran", patterns=["veteran"]),
    Answer(question="Disability status", answer="I do not want to answer", patterns=["disability"]),
]


def _seed() -> Profile:
    """First run: prefill what the primary resume says. You review it in the Profile tab."""
    from . import resume
    try:
        text = resume.lane_texts()[load("resumes")["primary"]]
    except Exception:
        text = ""
    name = (text.strip().splitlines() or [""])[0].title().split()
    find = lambda p: (re.search(p, text, re.I) or [""])[0]  # noqa: E731
    p = Profile(
        first_name=name[0] if name else "", last_name=" ".join(name[1:]),
        email=find(r"[\w.+-]+@[\w-]+\.[\w.]+"), phone=find(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"),
        linkedin=find(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+"), github=find(r"(?:https?://)?github\.com/[\w-]+"),
        answers=DEFAULT_ANSWERS,
    )
    if "Master of Science in Computer Science" in text:
        p.degree, p.major = "Master of Science", "Computer Science"
    return p


def get() -> Profile:
    if not PATH.exists():
        return save(_seed())
    p = Profile(**(yaml.safe_load(PATH.read_text()) or {}))
    # newer versions learn more ways forms phrase the same question: add those patterns to your matching answers
    defaults = {a.question: a for a in DEFAULT_ANSWERS}
    for a in p.answers:
        if a.question in defaults:
            a.patterns += [x for x in defaults[a.question].patterns if x not in a.patterns]
    return p


def save(p: Profile) -> Profile:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(yaml.safe_dump(p.model_dump(), sort_keys=False, allow_unicode=True))
    return p


def _norm(s: str) -> str:
    return re.sub(r"\b(inc|llc|corp|corporation|co|ltd)\b|[^a-z0-9]", "", s.lower())


def worked_at(company: str, p: Profile) -> bool:
    c = _norm(company)
    return any(_norm(w) == c for w in p.worked_at if w.strip())


def answers_for(company: str, p: Profile) -> list[dict]:
    out = []
    for a in p.answers:
        q, ans = a.question.replace("{company}", company), a.answer
        pats = [x.replace("{company}", company.lower()) for x in a.patterns]
        if "{company}" in a.question and any("worked" in x or "employ" in x for x in a.patterns):
            ans = "Yes" if worked_at(company, p) else a.answer
        out.append({"question": q, "answer": ans, "patterns": pats, "note": a.note})
    return out


def match_answer(label: str, company: str, p: Profile) -> dict | None:
    """Best canned answer for a form question label (longest matching pattern wins)."""
    flat = lambda s: re.sub(r"\s+", " ", re.sub(r"[^\w\s{}]", " ", s.lower())).strip()  # noqa: E731  ignore punctuation
    l = flat(label)
    best, best_len = None, 0
    for a in answers_for(company, p):
        for pat in map(flat, a["patterns"]):
            if pat and _rx((pat,)).search(l) and len(pat) > best_len:
                best, best_len = a, len(pat)
    return best


def fields(p: Profile) -> list[dict]:
    full = f"{p.first_name} {p.last_name}".strip()
    rows = [("Full name", full), ("First name", p.first_name), ("Last name", p.last_name), ("Preferred name", p.preferred_name),
            ("Email", p.email), ("Phone", p.phone), ("Location", p.location), ("LinkedIn", p.linkedin), ("GitHub", p.github),
            ("Website", p.website), ("School", p.school), ("Degree", p.degree), ("Major", p.major), ("Graduation", p.graduation),
            ("GPA", p.gpa), ("Undergrad school", p.undergrad_school), ("Undergrad degree", p.undergrad_degree),
            ("Work authorization", p.work_authorization), ("Availability", p.availability)]
    return [{"label": k, "value": v} for k, v in rows if v]
