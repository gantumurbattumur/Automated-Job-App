"""User-adjustable filter settings + named presets, stored in data/ (gitignored)."""
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import DATA

SETTINGS = DATA / "settings.yaml"
PRESETS = DATA / "presets.yaml"

JobType = Literal["internship", "coop", "fulltime"]
Category = Literal["ai_ml", "data_science", "data_eng", "swe", "research", "quant", "hardware", "product", "design", "other"]
Education = Literal["undergrad_only", "masters_not_listed", "phd", "mba", "high_school"]
Cpt = Literal["friendly", "unclear", "blocked"]
Auth = Literal["citizenship", "green_card", "clearance", "export_control"]
WorkMode = Literal["remote", "hybrid", "onsite"]


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relevance: float = 0.55
    freshness: float = 0.30
    friendliness: float = 0.15
    personal: float = 0.15  # learned from your saves/applies vs "not interested"; ignored until trained


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # role
    job_types: list[JobType] = ["internship", "coop"]
    seasons: list[str] = []  # e.g. "summer 2027"; empty = any. Jobs with no detectable season are kept.
    hide_past_seasons: bool = True
    categories: list[Category] = ["ai_ml", "data_science", "data_eng", "swe", "research", "quant"]  # empty = any
    title_include: list[str] = []  # empty = any
    title_exclude: list[str] = ["senior", "staff", "principal", "lead"]
    hide_education: list[Education] = ["undergrad_only", "phd", "mba", "high_school"]
    # work authorization
    cpt_allow: list[Cpt] = ["friendly", "unclear"]
    hide_auth: list[Auth] = ["citizenship", "green_card", "clearance", "export_control"]
    no_future_sponsorship: Literal["warn", "hide", "ignore"] = "warn"
    h1b_boost: bool = True  # "CPT unclear" + H-1B sponsor history counts as likely friendly
    h1b_min_approvals: int = 5
    only_h1b_sponsors: bool = False
    cpt_issue_companies: Literal["demote", "hide", "ignore"] = "demote"  # companies you tagged "rejected over CPT"
    # company
    exclude_space: bool = True
    company_block: list[str] = []
    company_allow: list[str] = []  # boosted, never hidden by company rules
    hide_applied: bool = True
    hide_applied_companies: bool = False
    # location
    work_modes: list[WorkMode] = ["remote", "hybrid", "onsite"]
    us_only: bool = True
    locations_include: list[str] = []  # empty = anywhere
    near_location: str = ""  # "Los Angeles, CA" / "LA" -> only jobs within near_radius_miles (empty = off)
    near_radius_miles: int = 50
    near_include_remote: bool = True  # remote jobs pass the radius filter
    near_keep_unknown: bool = False  # keep jobs whose location can't be placed on a map ("Multiple locations")
    # time
    posted_within_days: int = 0  # 0 = any
    hide_expired: bool = True  # posting states an application deadline that has passed
    stale_days: int = 30  # warn when a posting has been open longer than this (0 = off)
    # pay (only applies when the posting lists an hourly rate)
    min_hourly: float = 0
    hide_unpaid: bool = True
    # match
    lane: str = "auto"  # auto | <lane name from config/resumes.yaml>
    min_score: float = 0
    min_relevance: float = 0  # percent 0-100
    skills_required: list[str] = []  # must mention at least one
    skills_excluded: list[str] = []
    # ranking
    weights: Weights = Weights()
    freshness_half_life_days: float = 5
    new_badge_hours: int = 48
    allow_boost: float = 10
    # tailoring: suggestions are written by Claude Code on your own plan (never the paid API)
    claude_model: Literal["sonnet", "opus", "haiku"] = "sonnet"
    # refresh & alerts
    schedule: Literal["daily", "interval"] = "daily"  # daily: everything once a morning; interval: the minute settings below
    daily_time: str = Field("06:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # local time
    refresh_simplify_minutes: int = 15  # 0 = off
    refresh_ats_minutes: int = 60
    notify: bool = True  # macOS notification for strong new matches
    notify_min_score: float = 35
    gmail_scan_minutes: int = 30  # only when Gmail is connected
    digest: bool = True  # one morning notification with the day's top new matches
    digest_hour: int = 8  # local time
    # maintenance
    prune_closed_days: int = 60  # delete closed postings (not in your tracker) after this many days
    weekly_backup: bool = True  # copy jobs.db to data/backups once a week (keeps 4)


DEFAULT_PRESETS = {
    "AI/ML, last 3 days": {"categories": ["ai_ml", "data_science", "research"], "posted_within_days": 3},
    "Everything CPT-friendly": {"cpt_allow": ["friendly"]},
    "SWE, last week": {"categories": ["swe"], "posted_within_days": 7, "lane": "swe"},
}


def _read(path) -> dict:
    return yaml.safe_load(path.read_text()) or {} if path.exists() else {}


def _write(path, data: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _known(d: dict) -> dict:
    """Drop keys this version doesn't know (renamed/removed settings) instead of refusing to start."""
    return {k: v for k, v in d.items() if k in Settings.model_fields}


def get() -> Settings:
    return Settings(**_known(_read(SETTINGS)))


def save(s: Settings) -> Settings:
    _write(SETTINGS, s.model_dump())
    return s


def reset() -> Settings:
    SETTINGS.unlink(missing_ok=True)
    return Settings()


def presets() -> dict[str, dict]:
    stored = _read(PRESETS)
    if not stored:  # first run: expand defaults into full settings so loading one is predictable
        stored = {k: Settings(**v).model_dump() for k, v in DEFAULT_PRESETS.items()}
        _write(PRESETS, stored)
    return stored


def save_preset(name: str, s: Settings) -> dict[str, dict]:
    p = presets()
    p[name] = s.model_dump()
    _write(PRESETS, p)
    return p


def delete_preset(name: str) -> dict[str, dict]:
    p = presets()
    p.pop(name, None)
    _write(PRESETS, p)
    return p
