from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Field, SQLModel, create_engine, Session

from .config import DB_PATH


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)  # "<source>:<company>:<ext id>"
    source: str
    company: str
    title: str
    location: str = ""
    url: str
    description: str = ""
    posted_at: datetime
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: Optional[datetime] = None
    closed: bool = False  # vanished from its source, or marked inactive there
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))  # source-specific tags (terms, degrees, sponsorship…)


class JobVector(SQLModel, table=True):
    job_id: str = Field(primary_key=True)
    text_hash: str
    vec: bytes  # float32 embedding


STATUSES = ["saved", "opened", "applied", "oa", "interview", "offer", "rejected", "withdrawn", "ghosted", "dismissed"]
PROGRESS = {"saved": 0, "opened": 1, "applied": 2, "oa": 3, "interview": 4, "offer": 5}  # forward-only for auto updates


class Application(SQLModel, table=True):
    job_id: str = Field(primary_key=True, foreign_key="job.id")
    status: str = "saved"  # one of STATUSES
    applied_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    follow_up: Optional[str] = None  # YYYY-MM-DD
    source: str = "manual"  # manual | apply_prompt | gmail
    cpt_issue: bool = False  # you were turned down because of CPT / visa status
    notes: str = ""


class TailorItem(SQLModel, table=True):
    """A job you're tailoring your resume for (up to 10 at a time): its description, chosen resume, and the
    bullet suggestions pasted back from Claude with your accept/reject decisions."""
    id: str = Field(primary_key=True)  # job id, or "paste:<n>" for a pasted description
    job_id: Optional[str] = None
    company: str = ""
    title: str = ""
    url: str = ""
    jd_text: str = ""
    lane: str = ""  # resume to tailor (empty = the job's best match)
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))  # parsed suggestions + statuses
    baseline: Optional[float] = None  # match score when first analyzed, to show progress


class InboxItem(SQLModel, table=True):
    """A job-related email from the Gmail scan; matched ones update the tracker, the rest wait for review."""
    message_id: str = Field(primary_key=True)
    received_at: datetime
    sender: str
    subject: str
    snippet: str = ""
    kind: str  # applied | rejected | oa | interview | offer
    company_guess: str = ""
    role_guess: Optional[str] = ""  # from the full email at scan time (only a short snippet is stored)
    job_id: Optional[str] = None  # set when matched to a tracked application
    state: str = "new"  # new | applied_to_tracker | dismissed


engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"timeout": 60, "check_same_thread": False})


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(conn, _):
    # WAL lets the page read while the scheduler writes; busy_timeout makes concurrent writers wait instead of failing
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=60000")
    cur.close()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add new nullable/defaulted columns to existing tables."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    default = {"closed": " DEFAULT 0", "meta": " DEFAULT '{}'", "cpt_issue": " DEFAULT 0", "data": " DEFAULT '{}'"}.get(col.name, "")
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}{default}"))


def session() -> Session:
    return Session(engine)
