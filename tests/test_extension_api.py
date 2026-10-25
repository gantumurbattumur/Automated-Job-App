from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend import extension, models, profile, resume, tracker
from backend.models import Application, Job
from tests.test_resumes import TEXT
from tests.test_sources_feed import db  # noqa: F401  (fixture)

NOW = datetime.now(timezone.utc)


def _jobs():
    with models.session() as s:
        s.add(Job(id="greenhouse:acme:123", source="greenhouse", company="Acme AI", title="ML Intern", posted_at=NOW,
                  url="https://job-boards.greenhouse.io/acme/jobs/123", description="python pytorch"))
        s.add(Job(id="lever:beta:u1", source="lever", company="Beta", title="SWE Intern", posted_at=NOW,
                  url="https://jobs.lever.co/beta/0f2b6f4e-1111-2222-3333-444455556666"))
        s.add(Job(id="workday:micron:JR114094", source="workday", company="Micron Technology", title="AI Engineer Intern", posted_at=NOW,
                  url="https://micron.wd1.myworkdayjobs.com/External/job/Boise-ID---Main-Site/Intern---SMAI-Probe-AI-Engineer_JR114094"))
        s.commit()


def test_match_job_variants(db):  # noqa: F811
    _jobs()
    m = lambda u: (extension.match_job(u) or Job(id="none")).id  # noqa: E731
    assert m("https://job-boards.greenhouse.io/acme/jobs/123") == "greenhouse:acme:123"
    assert m("https://job-boards.greenhouse.io/acme/jobs/123?gh_src=abc#app") == "greenhouse:acme:123"
    assert m("https://acme.com/careers/apply?gh_jid=123") == "greenhouse:acme:123"  # company-hosted embed
    assert m("https://jobs.lever.co/beta/0f2b6f4e-1111-2222-3333-444455556666/apply") == "lever:beta:u1"
    assert m("https://micron.wd1.myworkdayjobs.com/en-US/External/job/Boise-ID---Main-Site/Intern---SMAI-Probe-AI-Engineer_JR114094/apply/applyManually") \
        == "workday:micron:JR114094"
    assert m("https://example.com/careers/42") == "none"


def test_context_and_result(db, monkeypatch, tmp_path):  # noqa: F811
    _jobs()
    monkeypatch.setattr(resume, "DATA", tmp_path)
    monkeypatch.setattr(resume, "CONF", tmp_path / "resumes.yaml")
    monkeypatch.setattr(resume, "STORE", tmp_path / "resume")
    monkeypatch.setattr(resume, "ROOT", tmp_path)
    monkeypatch.setattr(resume, "changed", lambda: None)
    resume.add("AI/ML", "r.txt", TEXT.encode())
    monkeypatch.setattr(profile, "get", lambda: profile.Profile(first_name="Gana", last_name="B", email="g@x.com",
                                                                 worked_at=["Acme AI"], answers=profile.DEFAULT_ANSWERS))
    from backend.api import app
    c = TestClient(app)
    ctx = c.get("/ext/context", params={"url": "https://job-boards.greenhouse.io/acme/jobs/123"}).json()
    assert ctx["job"]["id"] == "greenhouse:acme:123" and ctx["company"] == "Acme AI" and ctx["worked_here"]
    assert ctx["values"]["full_name"] == "Gana B" and ctx["resume"]["url"] == "/resume/ai_ml"
    assert any(p == "e-?mail" or "mail" in p for p, _ in ctx["fields"]) and "thank you" in ctx["confirm"]
    worked = next(a for a in ctx["answers"] if "worked for" in a["question"])
    assert worked["answer"] == "Yes" and "Acme AI" in worked["question"]
    unknown = c.get("/ext/context", params={"url": "https://x.com/jobs/1", "company": "Zeta"}).json()
    assert unknown["job"] is None and unknown["company"] == "Zeta" and "Zeta" in unknown["essay_prompt"]

    # results: filled is only logged; applied updates the tracker; unknown pages get one manual entry
    c.post("/ext/result", json={"url": "https://job-boards.greenhouse.io/acme/jobs/123", "status": "filled", "filled": 12})
    c.post("/ext/result", json={"url": "https://job-boards.greenhouse.io/acme/jobs/123", "status": "applied"})
    c.post("/ext/result", json={"url": "https://x.com/jobs/1", "status": "opened", "company": "Zeta", "title": "Data Intern"})
    assert not any(a["company"] == "Zeta" for a in tracker.rows())  # just looking doesn't create entries
    for _ in range(2):
        c.post("/ext/result", json={"url": "https://x.com/jobs/1", "status": "applied", "company": "Zeta", "title": "Data Intern"})
    with models.session() as s:
        assert s.get(Application, "greenhouse:acme:123").status == "applied"
        assert len([a for a in tracker.rows() if a["company"] == "Zeta"]) == 1
    assert c.get("/ext/runs").json()[0]["status"] == "applied"
    assert c.post("/ext/result", json={"url": "u", "status": "submitted"}).status_code == 422


def test_cors_allows_the_extension():
    from backend.api import app
    r = TestClient(app).options("/ext/context?url=x", headers={"Origin": "chrome-extension://abcdefghijklmnop",
                                                               "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "chrome-extension://abcdefghijklmnop"
