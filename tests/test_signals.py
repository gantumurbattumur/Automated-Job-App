import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import digest, h1b, maintenance, models, personal, relevance, settings, tracker
from backend.feed import _friendliness
from backend.filters import parse_deadline
from backend.models import Application, Job, JobVector
from backend.settings import Settings
from backend.sources import boards, workable
from tests.test_sources_feed import NOW, db  # noqa: F401  (fixture)


def test_workable_parse_and_discovery():
    ps = [{"shortcode": "AB12", "title": "Software Intern (Spring 2027)", "remote": True, "state": "published",
           "locations": [{"city": "Roanoke", "region": "Virginia", "countryCode": "US"}], "published": "2026-10-01T00:00:00Z"},
          {"shortcode": "CD34", "title": "Engineer", "state": "published"}]
    (j,) = workable.parse("tmeic", ps, "TMEIC")
    assert j.id == "workable:tmeic:AB12" and j.location == "Roanoke, Virginia, US (Remote)"
    assert boards.board_of("workable", "https://apply.workable.com/cogna/j/45A6283F88/apply") == "cogna"


def test_h1b_lookup(tmp_path, monkeypatch):
    cache = tmp_path / "h1b.json"
    cache.write_text(json.dumps({"metaplatforms": 8857, "microntechnology": 838, "f5": 224, "amazoncomservices": 9000,
                                 "amazonwebservices": 7000}))
    monkeypatch.setattr(h1b, "CACHE", cache)
    monkeypatch.setattr(h1b, "_sources", lambda: [])
    h1b._index.cache_clear(); h1b.approvals.cache_clear()
    assert h1b.approvals("Micron Technology, Inc.") == 838
    assert h1b.approvals("Meta") == 8857  # prefix of "Meta Platforms"
    assert h1b.approvals("Amazon") == 9000  # biggest matching entity
    assert h1b.approvals("F5") == 224 and h1b.approvals("K1") is None  # short names only match exactly
    h1b._index.cache_clear(); h1b.approvals.cache_clear()


def test_friendliness_uses_h1b_and_cpt_outcomes():
    st = Settings()
    assert _friendliness("unclear", 500, False, st)[0] == 0.75
    assert _friendliness("unclear", 2, False, st)[0] == 0.4
    assert _friendliness("friendly", None, False, st)[0] == 1.0
    assert _friendliness("friendly", 500, True, st) == (0.0, "rejected you over CPT before")
    assert _friendliness("unclear", 500, False, Settings(h1b_boost=False))[0] == 0.4


@pytest.mark.parametrize("text,expected", [
    ("Apply by December 1, 2026 to be considered", "2026-12-01"),
    ("Application deadline: Nov 15", "2026-11-15"),
    ("Applications close on 01/10/2027.", "2027-01-10"),
    ("Deadline to apply - March 3rd", "2027-03-03"),  # earlier than the posting date in 2026 -> next year
    ("We review applications on a rolling basis", None),
])
def test_deadlines(text, expected):
    d = parse_deadline(text, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert (d.date().isoformat() if d else None) == expected


def _row(i, cat, src="greenhouse"):
    return {"id": f"j{i}", "relevance_parts": {"semantic": 0.9 if cat == "ai_ml" else 0.2, "skills": 0.5, "title": 1.0},
            "_fresh": 0.5, "_friendly": 0.4, "h1b_approvals": 10, "has_description": True, "categories": [cat],
            "work_mode": "onsite", "source": src}


def test_personal_model_waits_for_feedback_then_learns():
    rows = [_row(i, "ai_ml") for i in range(6)] + [_row(i, "design") for i in range(6, 12)] + [_row(99, "ai_ml"), _row(98, "design")]
    few = {f"j{i}": "applied" for i in range(3)}
    p, info = personal.predict(rows, few)
    assert p is None and not info["active"] and info["needed"] == personal.MIN_EACH
    statuses = {**{f"j{i}": "saved" for i in range(6)}, **{f"j{i}": "dismissed" for i in range(6, 12)}}
    p, info = personal.predict(rows, statuses)
    assert info["active"] and p[-2] > 0.5 > p[-1]  # unseen AI/ML job liked, unseen design job not


def test_prune_keeps_tracked_and_recent(db):  # noqa: F811
    old = NOW - timedelta(days=90)
    with models.session() as s:
        for jid, closed, seen in [("a", True, old), ("b", True, old), ("c", True, NOW), ("d", False, old)]:
            s.add(Job(id=jid, source="t", company="c", title="Intern", url="u", posted_at=old, closed=closed, last_seen=seen))
            s.add(JobVector(job_id=jid, text_hash="h", vec=b""))
        s.add(Application(job_id="b", status="applied"))
        s.commit()
    assert maintenance.prune(60) == 1
    with models.session() as s:
        assert s.get(Job, "a") is None and s.get(JobVector, "a") is None
        assert all(s.get(Job, x) for x in "bcd")


def test_backup_rotation(tmp_path, monkeypatch):
    import sqlite3
    dbf = tmp_path / "jobs.db"
    sqlite3.connect(dbf).execute("create table t(x)").connection.commit()
    monkeypatch.setattr(maintenance, "DB_PATH", dbf)
    monkeypatch.setattr(maintenance, "BACKUPS", tmp_path / "backups")
    assert maintenance.backup() and maintenance.backup() is None  # second one within a week is skipped
    for d in range(6):
        (tmp_path / "backups" / f"jobs-2026-01-0{d + 1}.db").write_bytes(b"")
    maintenance.backup(force=True)
    assert len(list((tmp_path / "backups").glob("jobs-*.db"))) == maintenance.KEEP_BACKUPS


def test_migrate_old_title_exclude(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS", tmp_path / "settings.yaml")
    monkeypatch.setattr(settings, "PRESETS", tmp_path / "presets.yaml")
    settings._write(settings.SETTINGS, {"title_exclude": maintenance.OLD_TITLE_EXCLUDE, "posted_within_days": 3})
    settings._write(settings.PRESETS, {"Mine": {"title_exclude": maintenance.OLD_TITLE_EXCLUDE}, "Custom": {"title_exclude": ["x"]}})
    assert maintenance.migrate_settings()
    assert "manager" not in settings.get().title_exclude and settings.get().posted_within_days == 3
    assert settings._read(settings.PRESETS)["Custom"]["title_exclude"] == ["x"]  # your own lists are untouched


def test_digest_due_once_per_day_after_hour():
    today = datetime.now().date().isoformat()
    assert not digest.due({"date": today}, 0)
    assert digest.due({"date": "2000-01-01"}, 0) and digest.due(None, 0)
    assert not digest.due(None, 24)  # hour never reached


def test_cpt_issue_flag_roundtrip(db):  # noqa: F811
    with models.session() as s:
        s.add(Job(id="x", source="t", company="Acme", title="Intern", url="u", posted_at=NOW))
        s.commit()
    assert tracker.set_status("x", "rejected", cpt_issue=True).cpt_issue
    assert tracker.set_status("x", "rejected").cpt_issue  # unchanged when not passed


def test_resume_bullets_are_whole():
    text = "EXPERIENCE\nEngineer June 2024 - May 2025\n● Built a thing that does a lot of useful work across\nmany wrapped lines of text.\nSoftware Intern June 2024 - October 2024\n● Second bullet that is also long enough to count here.\nProject X | Github Link\n● Third one, long enough to be a bullet in the list ok."
    assert relevance._lines(text) == ["Built a thing that does a lot of useful work across many wrapped lines of text.",
                                      "Second bullet that is also long enough to count here.",
                                      "Third one, long enough to be a bullet in the list ok."]


def test_daily_schedule_runs_once_after_time_and_catches_up():
    from backend.refresh import daily_due
    at = lambda h, m: datetime(2026, 10, 21, h, m)  # noqa: E731
    assert not daily_due("06:00", None, at(5, 59))
    assert daily_due("06:00", None, at(6, 0))
    assert daily_due("06:00", {"date": "2026-10-20"}, at(9, 30))  # Mac was asleep at 6: runs on wake-up
    assert not daily_due("06:00", {"date": "2026-10-21"}, at(23, 0))  # once per day
    with pytest.raises(Exception):
        Settings(daily_time="6am")
    assert Settings().schedule == "daily" and Settings().daily_time == "06:00"
