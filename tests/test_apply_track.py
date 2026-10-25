from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from backend import gmail, models, profile, tracker
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


# --- profile & answers --------------------------------------------------------

def P(**kw):
    return profile.Profile(answers=profile.DEFAULT_ANSWERS, worked_at=["F5 Networks", "F5"], **kw)


def test_worked_here_answer_depends_on_company():
    p = P()
    ans = lambda co: next(a for a in profile.answers_for(co, p) if "worked for" in a["question"])["answer"]  # noqa: E731
    assert ans("F5") == "Yes" and ans("F5, Inc.") == "Yes" and ans("Stripe") == "No"
    assert not profile.worked_at("Pinecone", profile.Profile(worked_at=["Pinecone Academy"]))  # not the same company


def test_form_label_matching():
    p = P()
    m = lambda label, co="Stripe": (profile.match_answer(label, co, p) or {}).get("answer")  # noqa: E731
    assert m("Will you now or in the future require sponsorship for employment visa status?") == "Yes"
    assert m("Do you currently require visa sponsorship?") == "No"
    assert m("Are you legally authorized to work in the United States?") == "Yes"
    assert m("Have you ever worked for F5?", "F5") == "Yes"
    assert m("What is your gender?") == "Decline to self-identify"
    assert m("Tell us about a project you are proud of") is None


def test_profile_fields_skip_empty():
    labels = [f["label"] for f in profile.fields(P(first_name="Gana", email="g@x.com"))]
    assert "Email" in labels and "Phone" not in labels


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


# --- autofill helpers (the browser part is tested by hand: it never submits) ---
from backend import autofill  # noqa: E402


def test_pick_options():
    assert autofill._pick(["California State University - Chico", "California State University - Dominguez Hills"],
                          "California State University, Dominguez Hills") == "California State University - Dominguez Hills"
    assert autofill._pick(["Master of Business Administration (M.B.A.)", "Master's Degree"], "Master of Science") == "Master's Degree"
    assert autofill._pick(["Carson City, Nevada, United States", "Carson, California, United States"], "Carson, CA") == \
        "Carson, California, United States"
    assert autofill._pick(["Decline To Self Identify", "Male", "Female"], "Decline to self-identify") == "Decline To Self Identify"
    assert autofill._pick(["Yes", "No"], "Yes") == "Yes"
    assert autofill._pick(["Biology", "Chemistry"], "Computer Science") is None


def test_answer_for_labels():
    p = P(first_name="Gana", last_name="B", email="g@x.com", graduation="May 2028")
    v = autofill.values(p)
    a = lambda label, co="Stripe": autofill.answer_for(label, co, p, v)  # noqa: E731
    assert a("First Name*") == "Gana" and a("Email*") == "g@x.com" and a("End date year*") == "2028"
    assert a("Will you now or in the future require sponsorship?") == "Yes"  # canned answer beats field names
    assert a("Have you previously worked for F5?", "F5") == "Yes"
    assert a("Why do you want to work here?") is None


def test_apply_urls():
    assert autofill._apply_url("https://jobs.lever.co/acme/123") == "https://jobs.lever.co/acme/123/apply"
    assert autofill._apply_url("https://jobs.ashbyhq.com/acme/u1") == "https://jobs.ashbyhq.com/acme/u1/application"
    assert autofill._apply_url("https://job-boards.greenhouse.io/acme/jobs/1") == "https://job-boards.greenhouse.io/acme/jobs/1"
    assert autofill.CONFIRM.search("Thank you for applying! We'll be in touch.")


def test_decline_options_and_more_phrasings():
    assert autofill._pick(["Male", "Female", "I don't wish to answer"], "Decline to self-identify") == "I don't wish to answer"
    assert autofill._pick(["Yes", "No", "Prefer not to say"], "I do not want to answer") == "Prefer not to say"
    p = P(location="Carson, CA", major="Computer Science")
    v = autofill.values(p)
    assert autofill.answer_for("Where are you currently based?", "x", p, v) == "Carson, CA"
    assert autofill.answer_for("What course are you studying?", "x", p, v) == "Computer Science"
    assert autofill.answer_for("Do you require any employment authorization sponsorship now or in the future?", "x", p, v) == "Yes"


def test_required_markers_and_punctuation():
    p = P(first_name="Gana", last_name="B")
    v = autofill.values(p)
    assert autofill.answer_for("Full name ✱", "x", p, v) == "Gana B"
    assert autofill.answer_for("Do you now, or will you in the future, require immigration sponsorship?", "x", p, v) == "Yes"


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


def test_education_years_for_autofill():
    p = P(start="August 2026", graduation="May 2028")
    v = autofill.values(p)
    assert autofill.answer_for("Start date year*", "x", p, v) == "2026"
    assert autofill.answer_for("End date year*", "x", p, v) == "2028"


def test_bay_area_hybrid_question_is_not_answered_yes():
    p = P()
    assert profile.match_answer("Are you located in the San Francisco Bay Area and able to work hybrid 3 days a week?", "x", p) is None
    assert profile.match_answer("Are you able to work on-site?", "x", p)["answer"] == "Yes"


def test_full_legal_name():
    p = P(first_name="Gana", last_name="B")
    v = autofill.values(p)
    for label in ("Full Legal Name", "Legal name", "Name", "Your full name"):
        assert autofill.answer_for(label, "x", p, v) == "Gana B", label
