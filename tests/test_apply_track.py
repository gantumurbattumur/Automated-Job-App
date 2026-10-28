from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from backend import gmail, models, tracker
from backend.models import Application, InboxItem, Job

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(models, "engine", engine)
    with models.session() as s:
        for i, co in enumerate(["Stripe", "F5", "Scale AI"]):
            s.add(Job(id=f"j{i}", source="t", company=co, title="SWE Intern", url="u", posted_at=NOW))
        s.commit()
    return engine


# --- tracker ------------------------------------------------------------------

def test_status_flow_and_timestamps(db):
    a = tracker.set_status("j0", "opened")
    assert a.opened_at and not a.applied_at
    a = tracker.set_status("j0", "applied", notes="via Simplify", follow_up="2026-11-01")
    assert a.applied_at and a.notes == "via Simplify" and a.follow_up == "2026-11-01"
    with pytest.raises(ValueError):
        tracker.set_status("j0", "maybe")
    with pytest.raises(KeyError):
        tracker.set_status("nope", "applied")


def test_auto_updates_never_go_backwards(db):
    tracker.set_status("j0", "interview")
    assert tracker.set_status("j0", "applied", "gmail", forward_only=True).status == "interview"
    tracker.set_status("j1", "rejected")
    assert tracker.set_status("j1", "oa", "gmail", forward_only=True).status == "rejected"
    assert tracker.set_status("j1", "applied").status == "applied"  # you can always fix it by hand


def test_ghosted_after_30_days_and_stats(db):
    tracker.set_status("j0", "applied")
    tracker.set_status("j1", "interview")
    with models.session() as s:
        a = s.get(Application, "j0")
        a.updated_at = NOW - timedelta(days=31)
        s.add(a)
        s.commit()
    assert {r["job_id"]: r["status"] for r in tracker.rows()}["j0"] == "ghosted"
    st = tracker.stats()
    assert st["applied_total"] == 2 and st["by_status"]["interview"] == 1 and st["response_rate"] == 0.5
    assert len(st["applied_by_day"]) == 7 and st["applied_by_day"][-1] == 2  # both applied today (Home's week chart)


def test_manual_entry_and_csv(db):
    a = tracker.add_manual("Acme", "ML Intern", status="applied")
    assert a.job_id.startswith("manual:")
    with models.session() as s:
        assert s.get(Job, a.job_id).closed  # never shows up in the job feed
    assert "Acme,ML Intern,applied" in tracker.to_csv()


# --- gmail --------------------------------------------------------------------

@pytest.mark.parametrize("subject,body,kind", [
    ("Thank you for applying to Stripe", "We received your application", "applied"),
    ("Your application to Scale AI", "Unfortunately, we have decided not to move forward. We enjoyed the interview.", "rejected"),
    ("Next steps: HackerRank assessment", "Please complete the coding assessment", "oa"),
    ("Interview invitation - SWE Intern", "We'd like to schedule an interview", "interview"),
    ("Your offer", "We are pleased to offer you the internship", "offer"),
    ("Weekly newsletter", "Top stories this week", None),
])
def test_classify(subject, body, kind):
    assert gmail.classify(subject, body) == kind


def test_match_company():
    companies = {"Stripe": ["j0"], "F5": ["j1"], "Scale AI": ["j2"]}
    m = lambda sender, subject, body="": gmail.match_company(sender, subject, body, companies)  # noqa: E731
    assert m("Stripe <no-reply@stripe.com>", "Thanks for applying") == "j0"
    assert m("no-reply@greenhouse.io", "Thank you for applying to Scale AI!") == "j2"
    assert m("careers@f5.com", "Application received") == "j1"
    assert m("noreply@acme.com", "Thanks for applying to Acme") is None
    assert m("x@y.com", "Our stripes collection sale") is None  # whole-word only


def test_ingest_updates_tracker_and_queues_unknown(db):
    tracker.set_status("j0", "applied")
    msgs = [{"message_id": "1", "sender": "Stripe <a@stripe.com>", "subject": "Interview invitation",
             "date": NOW, "body": "We'd like to schedule an interview"},
            {"message_id": "2", "sender": "Acme Careers <jobs@acme.com>", "subject": "Thank you for applying to Acme",
             "date": NOW, "body": ""},
            {"message_id": "3", "sender": "news@x.com", "subject": "Sale", "date": NOW, "body": "50% off"}]
    assert gmail.ingest(msgs) == {"scanned": 3, "job_emails": 2, "updated": 1, "to_review": 1}
    assert gmail.ingest(msgs)["job_emails"] == 0  # already seen
    with models.session() as s:
        assert s.get(Application, "j0").status == "interview"
        item = s.get(InboxItem, "2")
        assert item.state == "new" and item.company_guess == "Acme"


# --- email -> company / role / suggestions ---------------------------------------

@pytest.mark.parametrize("sender,subject,body,company,role", [
    ("Workday at Gilead <gilead@myworkday.com>", "Thank you for your interest in Gilead Sciences",
     "Thank you for your interest in the R0054868 Intern - CFO - IT (Digital Marketing) opportunity at Gilead",
     "Gilead Sciences", "Intern - CFO - IT (Digital Marketing)"),
    ("IBM Talent Acquisition <talent@ibm.com>", "Your IBM Application Status",
     "IBM Careers Ref: 129782 - Back End Developer Intern 2027 - RTP Dear Applicant, We regret", "IBM", "Back End Developer Intern 2027"),
    ("no-reply@greenhouse.io", "Your application to Scale AI",
     "Thank you for applying to the Machine Learning Research Intern position at Scale AI.", "Scale AI", "Machine Learning Research Intern"),
    ("Acme Recruiting <jobs@acme.com>", "Data Science Intern at Acme - next steps", "Please complete the assessment.",
     "Acme", "Data Science Intern"),
    ("hr@bigco.com", "Update", "We have an update for you.", "Bigco", None),
])
def test_email_company_and_role(sender, subject, body, company, role):
    assert gmail.guess_company(sender, subject, body) == company
    assert gmail.extract_role(subject, body) == role


def test_suggestions_rank_tracked_and_title(db):
    with models.session() as s:
        s.add(Job(id="g1", source="workday", company="Gilead Sciences", title="Intern - CFO - IT (Digital Marketing)", url="u", posted_at=NOW))
        s.add(Job(id="g2", source="workday", company="Gilead Sciences", title="Intern - Clinical Data Science", url="u", posted_at=NOW))
        s.add(Job(id="g3", source="simplify", company="Gilead", title="Intern - Finance", url="u", posted_at=NOW))
        s.commit()
    tracker.set_status("g3", "applied")
    top = gmail.suggestions("Gilead Sciences", "Intern - CFO - IT (Digital Marketing)")
    assert top[0]["job_id"] == "g1" and {x["job_id"] for x in top} == {"g1", "g2", "g3"}
    assert gmail.suggestions("Gilead", None)[0]["job_id"] == "g3"  # no role: the one you applied to wins
    assert gmail.suggestions("Nobody", "Intern") == []


def test_backfill_unknown_roles(db):
    a = tracker.add_manual("IBM", "", status="rejected")
    with models.session() as s:
        s.get(Job, a.job_id).title = "(unknown role)"
        s.add(InboxItem(message_id="m1", received_at=NOW, sender="talent@ibm.com", subject="Your IBM Application Status",
                        snippet="Ref: 129782 - Back End Developer Intern 2027 - RTP", kind="rejected", job_id=a.job_id,
                        state="applied_to_tracker"))
        s.commit()
    assert gmail.backfill_roles() == 1
    with models.session() as s:
        assert s.get(Job, a.job_id).title == "Back End Developer Intern 2027"


