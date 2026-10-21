import re
from datetime import datetime, timedelta, timezone

from ..config import load
from ..filters import _word


def is_intern(title: str) -> bool:
    jt = load("rules")["job_type"]
    return _word(title.lower(), jt["internship"] + jt["coop"])


def iso(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def ms(v) -> datetime:
    return datetime.fromtimestamp(int(v) / 1000, timezone.utc)


def workday_posted(text: str, now: datetime | None = None) -> datetime:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> midnight UTC of that day."""
    today = (now or datetime.now(timezone.utc)).replace(hour=0, minute=0, second=0, microsecond=0)
    t = (text or "").lower()
    if "today" in t:
        return today
    if "yesterday" in t:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", t)
    return today - timedelta(days=int(m[1]) if m else 30)
