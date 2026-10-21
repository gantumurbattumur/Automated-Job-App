"""Morning digest: the best postings that appeared in the last day."""
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from . import feed, settings


def top(hours: int = 24, n: int = 10) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = [r for r in feed.build_cached(settings.get()) if not r["hidden_reasons"]]
    fresh = [r for r in rows if _aware(r["first_seen"]) >= cutoff or r["posted_at"] >= cutoff]
    return fresh[:n]


def notify() -> dict:
    jobs = top()
    if sys.platform == "darwin":
        best = jobs[0] if jobs else None
        msg = (f"Your morning list is ready: {len(jobs)} new matches. Top: {best['title']} · {best['company']} ({best['score']:g})"
               if best else "Your morning list is ready: no new matches since yesterday.")
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "JobPrep: today\'s matches"', "-e", "end run", msg],
                       check=False, capture_output=True)
    return {"last_run": datetime.now(timezone.utc), "date": datetime.now().date().isoformat(), "count": len(jobs)}


def due(last: dict | None, hour: int) -> bool:
    now = datetime.now()  # local time: "8 am" means your 8 am
    return now.hour >= hour and (not last or last.get("date") != now.date().isoformat())


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
