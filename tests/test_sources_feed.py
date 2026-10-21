from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from backend import feed, models, refresh, resume
from backend.filters import classify
from backend.models import Job
from backend.settings import Settings
from backend.sources import enrich, simplify_list

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def listing(**kw):
    return {"id": "abc", "company_name": "Scale AI", "title": "ML Intern", "url": "https://jobs.lever.co/scale/" + "a" * 36,
            "date_posted": int(NOW.timestamp()), "active": True, "is_visible": True, "locations": ["San Francisco, CA"],
            "terms": ["Summer 2027"], "degrees": ["Bachelor's", "Master's"], "sponsorship": "Other", "category": "AI/ML/Data", **kw}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(models, "engine", engine)
    fake_embeddings(monkeypatch, {"ai_ml": "python machine learning pytorch models", "swe": "java backend spring services"})
    feed.invalidate()
    return engine


VOCAB = ["python", "machine", "learning", "pytorch", "models", "java", "backend", "spring", "services", "design", "figma", "tax"]


def fake_embeddings(monkeypatch, lanes):
    """Deterministic bag-of-words vectors instead of downloading the real model in tests."""
    import numpy as np
    from backend import relevance

    def embed(texts):
        v = np.array([[t.lower().count(w) for w in VOCAB] for t in texts], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    monkeypatch.setattr(resume, "lane_texts", lambda: lanes)
    monkeypatch.setattr(relevance, "embed", embed)
    relevance.lanes.cache_clear()
    monkeypatch.setattr(relevance, "_chunks", lambda text: [text])


def test_simplify_parse_keeps_only_active_visible():
    jobs = simplify_list.parse([listing(), listing(id="x", active=False), listing(id="y", is_visible=False)])
    assert [j.id for j in jobs] == ["simplify:abc"]
    j = jobs[0]
    assert j.location == "San Francisco, CA" and j.posted_at == NOW and j.meta["terms"] == ["Summer 2027"]


def test_simplify_parse_rejects_format_change():
    with pytest.raises(ValueError):
        simplify_list.parse([{"name": "something else"}])


def test_simplify_tags_drive_classification():
    meta = lambda **kw: {**simplify_list.parse([listing(**kw)])[0].meta}  # noqa: E731
    f = classify("ML Intern", "x", "", "NYC", meta=meta())
    assert f.seasons == ["summer 2027"] and {"ai_ml", "data_science"} <= set(f.categories) and f.education == []
    assert classify("Intern", "x", "", meta=meta(sponsorship="U.S. Citizenship is Required")).auth == ["citizenship"]
    assert classify("Intern", "x", "", meta=meta(sponsorship="Does Not Offer Sponsorship")).no_future_sponsorship
    assert classify("Intern", "x", "", meta=meta(sponsorship="Offers Sponsorship")).cpt == "friendly"
    assert classify("Intern", "x", "", meta=meta(degrees=["Bachelor's"])).education == ["masters_not_listed"]
    assert classify("Intern", "x", "", meta=meta(degrees=["PhD"])).education == ["phd"]
    assert classify("Hardware Intern", "x", "", meta=meta(category="Hardware")).categories == ["hardware"]


def test_enrich_url_mapping():
    assert enrich.api_url("https://job-boards.greenhouse.io/stripe/jobs/123") == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/123"
    assert enrich.api_url("https://jobs.lever.co/acme/" + "b" * 36).startswith("https://api.lever.co/v0/postings/acme/")
    assert enrich.api_url("https://acme.wd1.myworkdayjobs.com/x") is None


def test_upsert_adds_updates_and_closes(db):
    mk = lambda i, co="acme": Job(id=f"greenhouse:{co}:{i}", source="greenhouse", company=co, title="Intern", url="u", posted_at=NOW)  # noqa: E731
    added, closed = refresh._upsert("greenhouse", [mk(1), mk(2), mk(9, "other")], None, NOW)
    assert len(added) == 3 and closed == 0
    # next run: acme only returns job 1; "other" wasn't fetched (scope), so its job must stay open
    added, closed = refresh._upsert("greenhouse", [mk(1)], {"acme"}, NOW + timedelta(hours=1))
    assert added == [] and closed == 1
    with models.session() as s:
        assert s.get(Job, "greenhouse:acme:2").closed and not s.get(Job, "greenhouse:other:9").closed
        assert s.get(Job, "greenhouse:acme:1").last_seen is not None


def test_feed_collapses_cross_source_duplicates(db):
    gh = Job(id="greenhouse:scaleai:1", source="greenhouse", company="scaleai", title="ML Intern (Summer 2027)",
             location="San Francisco, CA", url="gh", posted_at=NOW, description="machine learning pytorch python")
    sj = simplify_list.parse([listing(title="ML Intern (Summer 2027)")])[0]
    refresh._upsert("greenhouse", [gh], None, NOW)
    refresh._upsert("simplify", [sj], None, NOW)
    rows = feed.build(Settings(), NOW)
    assert len(rows) == 1 and rows[0]["source"] == "greenhouse"  # full description scores higher
    assert rows[0]["also"][0]["source"] == "simplify"


# --- step 3: ATS sources ----------------------------------------------------
from backend.sources import ashby, boards, lever, smartrecruiters, workday  # noqa: E402
from backend.sources.common import workday_posted  # noqa: E402


def test_lever_parse_interns_only():
    ps = [{"id": "1", "text": "ML Intern", "categories": {"allLocations": ["NYC", "SF"]}, "workplaceType": "remote",
           "hostedUrl": "h", "createdAt": int(NOW.timestamp() * 1000), "descriptionPlain": "python",
           "lists": [{"text": "Req", "content": "<li>pytorch</li>"}]},
          {"id": "2", "text": "Head of Product", "createdAt": 0}]
    (j,) = lever.parse("acme", ps, "Acme")
    assert j.id == "lever:acme:1" and j.company == "Acme" and j.location == "NYC; SF (Remote)"
    assert "pytorch" in j.description and j.posted_at == NOW and j.meta["board"] == "acme"


def test_ashby_parse():
    ps = [{"id": "u1", "title": "Software Engineering Intern ", "location": "SF", "secondaryLocations": [{"location": "NYC"}],
           "publishedAt": "2026-10-20T00:00:00+00:00", "isListed": True, "jobUrl": "j", "descriptionPlain": "d",
           "compensation": {"scrapeableCompensationSalarySummary": "$50/hr"}},
          {"id": "u2", "title": "Data Intern", "isListed": False}]
    (j,) = ashby.parse("acme", ps)
    assert j.title == "Software Engineering Intern" and j.location == "SF; NYC" and "$50/hr" in j.description


def test_workday_parse_and_dates():
    ps = [{"title": "Data Science Intern", "externalPath": "/job/Austin/Data-Science-Intern_R-1", "locationsText": "2 Locations",
           "postedOn": "Posted 3 Days Ago"},
          {"title": "Principal Architect", "externalPath": "/job/x_R-2", "postedOn": "Posted Today"}]
    (j,) = workday.parse("acme|wd5|Careers", ps)
    assert j.id == "workday:acme:R-1" and j.url == "https://acme.wd5.myworkdayjobs.com/Careers/job/Austin/Data-Science-Intern_R-1"
    assert j.location == ""  # "2 Locations" is filled in by enrichment
    assert workday_posted("Posted Today", NOW) == NOW.replace(hour=0)
    assert workday_posted("Posted 30+ Days Ago", NOW) == NOW - timedelta(days=30)
    assert classify("Intern", "x", "", "2 Locations").us is None


def test_smartrecruiters_parse():
    ps = [{"id": "77", "name": "Software Intern", "location": {"city": "Austin", "region": "TX", "country": "us"},
           "releasedDate": "2026-10-19T00:00:00Z", "company": {"name": "Acme"}}]
    (j,) = smartrecruiters.parse("Acme1", ps)
    assert j.location == "Austin, TX, US" and j.url == "https://jobs.smartrecruiters.com/Acme1/77"


def test_board_discovery_from_urls():
    assert boards.board_of("greenhouse", "https://job-boards.greenhouse.io/stripe/jobs/1") == "stripe"
    assert boards.board_of("greenhouse", "https://boards.greenhouse.io/embed/job_app?token=1") is None
    assert boards.board_of("ashby", "https://jobs.ashbyhq.com/hcompany/856b1025-791f-46ed-9ea0-a88bb2f32ce5/application") == "hcompany"
    assert boards.board_of("workday", "https://aep.wd1.myworkdayjobs.com/en-US/AEPCareerSite/job/Columbus-OH/X_R1") == "aep|wd1|AEPCareerSite"
    assert boards.board_of("smartrecruiters", "https://jobs.smartrecruiters.com/Laxir1/743999834183955") == "Laxir1"


def test_enrich_supports_each_ats():
    assert enrich.supported("https://jobs.ashbyhq.com/acme/856b1025-791f-46ed-9ea0-a88bb2f32ce5")
    assert enrich.supported("https://aep.wd1.myworkdayjobs.com/AEPCareerSite/job/Columbus-OH/X_R1")
    assert not enrich.supported("https://aep.wd1.myworkdayjobs.com/AEPCareerSite")  # board page, not a posting
    assert not enrich.supported("https://www.tesla.com/careers/123")


def test_upsert_keeps_earliest_posted_date(db):
    mk = lambda d: Job(id="workday:acme:R1", source="workday", company="acme", title="Intern", url="u", posted_at=d)  # noqa: E731
    refresh._upsert("workday", [mk(NOW - timedelta(days=3))], None, NOW)
    refresh._upsert("workday", [mk(NOW)], None, NOW)  # "Posted 3 days ago" re-read later must not reset to today
    with models.session() as s:
        assert s.get(Job, "workday:acme:R1").posted_at.replace(tzinfo=timezone.utc) == NOW - timedelta(days=3)



# --- step 4: relevance ------------------------------------------------------
from backend import relevance  # noqa: E402


def _job(i, title, desc=""):
    return Job(id=f"t:{i}", source="t", company="c", title=title, url="u", posted_at=NOW, description=desc)


def test_relevance_ranks_ml_over_design_and_picks_lane(db):
    jobs = [_job(1, "Machine Learning Intern", "python pytorch models"), _job(2, "Design Intern", "figma design"),
            _job(3, "Backend Intern", "java spring backend services"), _job(4, "Tax Intern", "tax")]
    r = relevance.score(jobs, "ai_ml", 0.1)
    assert r["t:1"]["relevance"] > r["t:2"]["relevance"] and r["t:1"]["relevance"] > r["t:4"]["relevance"]
    assert r["t:1"]["lane"] == "ai_ml" and r["t:3"]["lane"] == "swe"
    assert "pytorch" in r["t:1"]["matched"] and r["t:3"]["missing"] == []  # swe lane knows java/spring
    assert relevance.score(jobs, "ai_ml", 0.1, forced="ai_ml")["t:3"]["lane"] == "ai_ml"


def test_vectors_are_cached_until_text_changes(db, monkeypatch):
    calls = []
    real = relevance.embed
    monkeypatch.setattr(relevance, "embed", lambda t: calls.append(len(t)) or real(t))
    j = _job(1, "ML Intern", "python")
    relevance.ensure_vectors([j]); relevance.ensure_vectors([j])
    j.description = "python pytorch"
    relevance.ensure_vectors([j])
    assert calls == [1, 1]


def test_skills_aliases_and_word_boundaries():
    assert relevance.skills_in("Experience with PyTorch, k8s and Retrieval-Augmented Generation") >= {"pytorch", "kubernetes", "rag"}
    assert relevance.skills_in("We go above and beyond in R&D, Series C, TS/SCI clearance, node graphs") == set()
    assert relevance.skills_in("C# and C++ tooling") == {"c#", "c++"}


def test_dedupe_key_ignores_season_decorations():
    k = lambda co, t: feed._key(Job(id="x", source="s", company=co, title=t, url="u", posted_at=NOW))  # noqa: E731
    assert k("Scale AI", "Machine Learning Research Intern (Summer 2027)") == k("scaleai", "Machine Learning Research Intern")
    assert k("Amplify", "Summer 2027: AI Scientist Intern") == k("Amplify", "AI Scientist Intern")
    assert k("Acme", "Data Intern") != k("Acme", "ML Intern")
