import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from .config import load
from .settings import Settings

SEASON = re.compile(r"\b(summer|fall|autumn|spring|winter)\s*[-'’]?\s*(20\d\d)\b", re.I)
SEASON_TEXT = re.compile(SEASON.pattern + r"\s+(?:\w+\s+){0,2}(?:intern|internship|co-op)", re.I)
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DEADLINE = re.compile(
    r"(?:apply by|application deadline|deadline(?: to apply)?|applications? (?:are |is )?due|closes? on|"
    r"accepting applications (?:until|through)|applications? close)\s*(?:is|:|on|-)?\s*"
    rf"((?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*(?:20\d\d)?|\d{{1,2}}/\d{{1,2}}/(?:20)?\d\d)", re.I)
HOURLY = re.compile(r"\$\s?(\d{2,3}(?:\.\d{1,2})?)\s*(?:(?:-|–|to)\s*\$?\s?(\d{2,3}(?:\.\d{1,2})?))?\s*(?:/|per\s+)\s*(?:hour|hr)\b", re.I)


def _has(text: str, terms: list[str]) -> list[str]:
    t = text.lower()
    return [x for x in terms if x in t]


@lru_cache(maxsize=512)
def _rx(terms: tuple[str, ...]) -> re.Pattern:
    alts = sorted({re.escape(x.lower()) for x in terms if x}, key=len, reverse=True)  # longest phrase wins
    return re.compile(rf"(?<!\w)(?:{'|'.join(alts)})(?!\w)" if alts else r"(?!)")


def _words(text: str, terms: list[str]) -> list[str]:
    """Whole-word/phrase hits, so "itar" doesn't match "guitar". One precompiled regex per term list."""
    return list(dict.fromkeys(m.group(0) for m in _rx(tuple(terms)).finditer(text)))


def _word(text: str, terms: list[str]) -> bool:
    return _rx(tuple(terms)).search(text) is not None


SEASON_END = {"winter": 3, "spring": 5, "summer": 8, "fall": 12}  # month the season is over


def season_past(season: str, now: datetime) -> bool:
    name, year = season.split()
    return (int(year), SEASON_END[name]) < (now.year, now.month)


def _is_us(location: str, rules: dict) -> bool | None:
    """True if any listed location is in the US; None when it says nothing (empty, or just "Remote")."""
    if not re.sub(r"[^a-z]", "", location.lower().replace("remote", "")) or re.fullmatch(r"\s*(\d+|multiple|various|several|many)\s+locations?\s*|\s*(flexible|anywhere|tbd|n/?a)\s*", location.lower()):
        return None
    codes = re.compile(rf"(?:,|\s)\s*(?:{'|'.join(rules['us_state_codes'])})\b")
    for seg in re.split(r"[;•|/]| or ", location):
        low = seg.lower()
        if _word(low, rules["non_us_locations"]) and not re.search(r"\b(usa|united states)\b", low):
            continue  # e.g. "Cambridge, UK" is not Cambridge, MA
        if _word(low, rules["us_locations"]) or codes.search(seg):
            return True
    return False


def _hard(text: str, terms: list[str], soft: list[str]) -> bool:
    """True if some mention of `terms` is NOT softened by nearby wording like "may need" / "or otherwise eligible"."""
    for m in _rx(tuple(terms)).finditer(text):
        window = text[max(0, m.start() - 160): m.end() + 160]
        if not _word(window, soft):
            return True
    return False


def parse_deadline(text: str, posted: datetime) -> datetime | None:
    m = DEADLINE.search(text)
    if not m:
        return None
    raw = re.sub(r"(st|nd|rd|th),?", " ", m[1].lower()).replace(",", " ").replace(".", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    for fmt in ("%b %d %Y", "%B %d %Y", "%b %d", "%B %d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            d = datetime.strptime(raw.replace("sept", "sep"), fmt)
        except ValueError:
            continue
        if "%Y" not in fmt and "%y" not in fmt:  # no year: the next such date after posting
            d = d.replace(year=posted.year)
            if d.date() < posted.date():
                d = d.replace(year=posted.year + 1)
        return d.replace(tzinfo=posted.tzinfo)
    return None


@dataclass
class Flags:
    job_type: str  # internship | coop | fulltime
    cpt: str  # friendly | unclear | blocked
    cpt_hits: list[str]
    auth: list[str]  # citizenship | green_card | clearance | export_control
    no_future_sponsorship: bool
    space: bool
    education: list[str]
    categories: list[str]
    seasons: list[str]
    work_mode: str  # remote | hybrid | onsite
    us: bool | None  # None = location unknown
    hourly: tuple[float, float] | None
    unpaid: bool
    warnings: list[str] = field(default_factory=list)


SIMPLIFY_CATEGORY = {"ai/ml/data": ["ai_ml", "data_science"], "data science, ai & machine learning": ["ai_ml", "data_science"],
                     "software": ["swe"], "software engineering": ["swe"], "hardware": ["hardware"],
                     "hardware engineering": ["hardware"], "product": ["product"], "product management": ["product"],
                     "quant": ["quant"], "quantitative finance": ["quant"]}


def classify(title: str, company: str, description: str, location: str = "", rules: dict | None = None,
             meta: dict | None = None) -> Flags:
    rules = rules or load("rules")
    t, d, loc = title.lower(), description.lower(), location.lower()

    job_type = ("coop" if _word(t, rules["job_type"]["coop"])
                else "internship" if _word(t, rules["job_type"]["internship"]) else "fulltime")

    auth_hits = {k: _words(d, v) for k, v in rules["auth"].items()}
    friendly = _words(d, rules["cpt_friendly"])
    # explicit CPT/F-1 negatives always win over friendly words
    cpt = "blocked" if auth_hits["no_cpt"] else "friendly" if friendly else "unclear"
    meta = meta or {}
    sponsorship = (meta.get("sponsorship") or "").lower()  # SimplifyJobs tag
    if "citizenship is required" in sponsorship:
        auth_hits["citizenship"].append("simplify: citizenship required")
    if "does not offer" in sponsorship:
        auth_hits["no_future_sponsorship"].append("simplify: does not offer sponsorship")
    if sponsorship == "offers sponsorship" and cpt == "unclear":
        cpt, friendly = "friendly", ["simplify: offers sponsorship"]
    soft = rules.get("auth_soft_context", [])
    softened = [k for k in ("citizenship", "green_card", "export_control")
                if auth_hits[k] and not any(h.startswith("simplify:") for h in auth_hits[k]) and not _hard(d, rules["auth"][k], soft)]
    auth = [k for k in ("citizenship", "green_card", "clearance", "export_control") if auth_hits[k] and k not in softened]

    education = ({k for k, v in rules["education_title"].items() if _word(t, v)}
                 | {k for k, v in rules["education_text"].items() if _word(d, v)})
    degrees = set(meta.get("degrees") or [])
    if degrees and "Master's" not in degrees:
        education |= {"phd"} if degrees == {"PhD"} else {"mba"} if degrees == {"MBA"} else {"masters_not_listed"}
    education = sorted(education)
    categories = [k for k, v in rules["category"].items() if _word(t, v)]
    categories += [c for c in SIMPLIFY_CATEGORY.get((meta.get("category") or "").lower(), []) if c not in categories]
    categories = categories or ["other"]
    # the title is authoritative; descriptions also mention graduation dates ("graduating spring 2028"),
    # so only trust a description season that sits right before "intern…"
    found = SEASON.findall(title) or SEASON_TEXT.findall(description)
    seasons = sorted({f"{'fall' if m[0].lower() == 'autumn' else m[0].lower()} {m[1]}" for m in found}
                     or {x.lower() for x in meta.get("terms", []) if SEASON.fullmatch(x)})

    work_mode = "remote" if "remote" in loc or "remote" in t else "hybrid" if "hybrid" in loc or "hybrid" in d else "onsite"
    us = _is_us(location, rules)

    rates = [(float(a), float(b or a)) for a, b in HOURLY.findall(description)]
    hourly = (min(r[0] for r in rates), max(r[1] for r in rates)) if rates else None

    f = Flags(job_type, cpt, auth_hits["no_cpt"] or friendly, auth, bool(auth_hits["no_future_sponsorship"]),
              _word(company.lower(), rules["space"]["companies"]) or _word(d, rules["space"]["keywords"]),
              education, categories, seasons, work_mode, us, hourly, "unpaid" in d)
    if f.no_future_sponsorship:
        f.warnings.append("no sponsorship later (OPT/H-1B)")
    if softened:
        f.warnings.append("may require " + " / ".join(x.replace("_", " ") for x in softened))
    if "masters_not_listed" in education:
        f.warnings.append("Master's not listed")
    if _word(d, rules["warn"]["export_notice"]) and "export_control" not in auth:
        f.warnings.append("export-control notice")
    return f


def apply(title: str, company: str, location: str, description: str, posted_at: datetime, now: datetime,
          f: Flags, s: Settings, applied: bool, applied_company: bool) -> list[str]:
    """Return the reasons a job is hidden under settings `s` (empty list = shown)."""
    t, d, co = title.lower(), description.lower(), company.lower()
    allowed = _word(co, s.company_allow)
    r = []
    # role
    if f.job_type not in s.job_types:
        r.append(f"type: {f.job_type}")
    if s.hide_past_seasons and f.seasons and all(season_past(x, now) for x in f.seasons):
        r.append(f"past season: {', '.join(f.seasons)}")
    if s.seasons and f.seasons and not set(f.seasons) & {x.lower() for x in s.seasons}:
        r.append(f"season: {', '.join(f.seasons)}")
    if s.categories and not set(f.categories) & set(s.categories):
        r.append(f"category: {', '.join(f.categories)}")
    if s.title_include and not _word(t, s.title_include):
        r.append("title missing include words")
    if s.title_exclude and _word(t, s.title_exclude):
        r.append("title has excluded word")
    if hits := set(f.education) & set(s.hide_education):
        r.append(f"education: {', '.join(sorted(hits))}")
    # work authorization
    if f.cpt not in s.cpt_allow:
        r.append(f"CPT {f.cpt}")
    if hits := set(f.auth) & set(s.hide_auth):
        r.append(f"requires {', '.join(sorted(hits))}")
    if f.no_future_sponsorship and s.no_future_sponsorship == "hide":
        r.append("no future sponsorship")
    # company
    if not allowed:
        if s.exclude_space and f.space:
            r.append("space/defense")
        if _word(co, s.company_block):
            r.append("company blocked")
        if s.hide_applied_companies and applied_company:
            r.append("already applied to company")
    if s.hide_applied and applied:
        r.append("already tracked")
    # location
    if f.work_mode not in s.work_modes:
        r.append(f"work mode: {f.work_mode}")
    if s.us_only and f.us is False:
        r.append("outside US")
    if s.locations_include and not _has(location, s.locations_include) and f.work_mode != "remote":
        r.append("location not in list")
    # time
    if s.posted_within_days and (now - posted_at).total_seconds() > s.posted_within_days * 86400:
        r.append(f"older than {s.posted_within_days}d")
    # pay
    if s.min_hourly and f.hourly and f.hourly[1] < s.min_hourly:
        r.append(f"pay ${f.hourly[1]:g}/hr")
    if s.hide_unpaid and f.unpaid:
        r.append("unpaid")
    # skills
    if s.skills_required and not _word(d, s.skills_required):
        r.append("missing required skills")
    if s.skills_excluded and _word(d, s.skills_excluded):
        r.append("has excluded skill")
    return r
