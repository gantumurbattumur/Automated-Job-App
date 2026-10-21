"""Housekeeping: drop long-closed postings, keep weekly database backups, migrate old default settings."""
import sqlite3
from datetime import datetime, timedelta, timezone

from sqlmodel import delete, select

from .config import DATA, DB_PATH
from .models import Application, Job, JobVector, session

BACKUPS = DATA / "backups"
KEEP_BACKUPS = 4


def prune(days: int) -> int:
    """Delete postings closed for more than `days` (by last time seen) that aren't in your tracker."""
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with session() as s:
        tracked = {a.job_id for a in s.exec(select(Application)).all()}
        old = [j.id for j in s.exec(select(Job).where(Job.closed == True)).all()  # noqa: E712
               if j.id not in tracked and _aware(j.last_seen or j.first_seen) < cutoff]
        for i in range(0, len(old), 900):
            chunk = old[i:i + 900]
            s.exec(delete(JobVector).where(JobVector.job_id.in_(chunk)))
            s.exec(delete(Job).where(Job.id.in_(chunk)))
        s.commit()
    return len(old)


def backup(force: bool = False) -> str | None:
    """Copy the database to data/backups/jobs-YYYY-MM-DD.db if the newest backup is a week old. Returns the path."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    existing = sorted(BACKUPS.glob("jobs-*.db"))
    if existing and not force:
        newest = datetime.fromtimestamp(existing[-1].stat().st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - newest < timedelta(days=7):
            return None
    dest = BACKUPS / f"jobs-{datetime.now():%Y-%m-%d}.db"
    src = sqlite3.connect(DB_PATH)
    try:
        out = sqlite3.connect(dest)
        src.backup(out)  # consistent snapshot even while the app is writing
        out.close()
    finally:
        src.close()
    for old in sorted(BACKUPS.glob("jobs-*.db"))[:-KEEP_BACKUPS]:
        old.unlink()
    return str(dest)


OLD_TITLE_EXCLUDE = ["senior", "staff", "principal", "manager", "director", "lead"]


def migrate_settings() -> bool:
    """Early versions excluded "manager"/"director" titles by default, hiding Product Manager internships."""
    from . import settings
    changed = False
    raw = settings._read(settings.SETTINGS)
    if raw.get("title_exclude") == OLD_TITLE_EXCLUDE:
        raw["title_exclude"] = settings.Settings().title_exclude
        settings._write(settings.SETTINGS, raw)
        changed = True
    presets = settings._read(settings.PRESETS)
    for p in presets.values():
        if p.get("title_exclude") == OLD_TITLE_EXCLUDE:
            p["title_exclude"] = settings.Settings().title_exclude
            changed = True
    if changed and presets:
        settings._write(settings.PRESETS, presets)
    return changed


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
