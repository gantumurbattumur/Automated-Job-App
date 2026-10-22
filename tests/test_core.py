from datetime import datetime, timedelta, timezone

import pytest

from backend import ranking
from backend.filters import apply, classify
from backend.settings import Settings
from backend.sources import greenhouse

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def hidden(title="ML Intern", company="acme", desc="Build models.", location="New York, NY", age_days=1,
           applied=False, applied_company=False, **settings):
    f = classify(title, company, desc, location)
    return apply(title, company, location, desc, NOW - timedelta(days=age_days), NOW, f, Settings(**settings),
                 applied, applied_company)


# --- classification -------------------------------------------------------

def test_cpt_states():
    assert classify("ML Intern", "acme", "Build models.").cpt == "unclear"
    assert classify("ML Intern", "acme", "CPT students welcome").cpt == "friendly"
    assert classify("ML Intern", "acme", "F-1 welcome, but no CPT").cpt == "blocked"


def test_auth_categories_and_word_boundaries():
    f = classify("Intern", "acme", "Must be a U.S. citizen with an active clearance. Subject to ITAR.")
    assert set(f.auth) == {"citizenship", "clearance", "export_control"}
    assert classify("Intern", "acme", "We love guitar and focus on customers.").auth == []


def test_job_type_title_words():
    assert classify("International Compensation Partner", "acme", "").job_type == "fulltime"
    assert classify("Software Engineer, Internal Tools", "acme", "").job_type == "fulltime"
    assert classify("ML Research Intern (Summer 2027)", "acme", "").job_type == "internship"
    assert classify("Data Co-op", "acme", "").job_type == "coop"


def test_categories_seasons_mode_pay():
    f = classify("Machine Learning Intern - Summer 2027", "acme", "Pay: $45 - $55/hour. Hybrid.", "Remote - US")
    assert "ai_ml" in f.categories and f.seasons == ["summer 2027"]
    assert f.work_mode == "remote" and f.hourly == (45, 55) and f.us
    assert classify("Intern", "acme", "", "London, UK").us is False
    assert classify("Intern", "acme", "", "Doha, Qatar").us is False
    assert classify("Intern", "acme", "", "Somewhere Unlisted").us is False
    assert classify("Intern", "acme", "", "Seattle, San Francisco").us is True
    assert classify("Intern", "acme", "", "Hybrid - Austin, TX").us is True
    assert classify("Intern", "acme", "", "London; New York, NY").us is True  # any US option counts
    assert classify("Intern", "acme", "", "Cambridge, UK").us is False
    assert classify("Intern", "acme", "", "").us is None
    assert classify("Intern", "acme", "", "Remote").us is None
    assert classify("Intern", "acme", "", "Multiple Locations").us is None


def test_season_ignores_graduation_dates():
    assert classify("SWE Intern (Summer 2027)", "acme", "graduating spring 2028").seasons == ["summer 2027"]
    assert classify("SWE Intern", "acme", "graduating spring 2028").seasons == []
    assert classify("SWE Intern", "acme", "Join our Summer 2027 internship").seasons == ["summer 2027"]


def test_soft_auth_wording_is_a_warning():
    f = classify("SWE Intern", "acme", "Applicants for these positions may need to be U.S. persons under export rules.")
    assert f.auth == [] and any("may require" in w for w in f.warnings)
    f = classify("SWE Intern", "acme", "Must be a U.S. person or otherwise eligible for an export license.")
    assert "export_control" not in f.auth
    assert classify("SWE Intern", "acme", "U.S. citizenship required for this role.").auth == ["citizenship"]


def test_undergrad_title_and_space_company_words():
    assert "undergrad_only" in classify("Data Analytics Undergraduate Intern", "x", "").education
    assert classify("AI Intern", "Space Dynamics Laboratory", "").space
    assert not classify("AI Intern", "Spacelift", "").space


def test_export_boilerplate_is_only_a_warning():
    f = classify("SWE Intern", "datadog", "To conform to US export control regulations, candidates should be eligible")
    assert f.auth == [] and "export-control notice" in f.warnings
    assert classify("SWE Intern", "acme", "Must be a U.S. person under ITAR").auth == ["export_control"]


# --- filter engine: one check per group -----------------------------------

def test_defaults_show_plain_ml_internship():
    assert hidden() == []


def test_role_filters():
    assert hidden(title="Software Engineer") == ["type: fulltime"]
    assert hidden(title="Senior ML Intern")  # title_exclude
    assert hidden(title="PhD Intern, ML")  # education
    assert hidden(title="Design Intern") == ["category: design"]  # non-tech off by default
    assert hidden(title="Design Intern", categories=[]) == []
    assert hidden(title="ML Intern Summer 2027", seasons=["fall 2026"])
    assert hidden(title="ML Intern Summer 2026") == ["past season: summer 2026"]  # NOW is Oct 2026
    assert hidden(title="ML Intern Fall 2026") == []
    assert hidden(title="ML Intern Summer 2026", hide_past_seasons=False) == []
    assert hidden(title="ML Intern", seasons=["fall 2026"]) == []  # unknown season is kept
    assert hidden(title="ML Intern", title_include=["research"])


def test_work_authorization_filters():
    assert hidden(desc="Must be a US citizen") == ["requires citizenship"]
    assert hidden(desc="Must be a US citizen", hide_auth=[]) == []
    assert hidden(desc="no CPT") == ["CPT blocked"]
    assert hidden(cpt_allow=["friendly"]) == ["CPT unclear"]
    assert hidden(desc="We will not sponsor visas") == []  # default: warn only
    assert hidden(desc="We will not sponsor visas", no_future_sponsorship="hide") == ["no future sponsorship"]


def test_company_filters():
    assert hidden(company="spacex") == ["space/defense"]
    assert hidden(company="spacex", exclude_space=False) == []
    assert hidden(company="spacex", company_allow=["spacex"]) == []  # allowlist overrides company rules
    assert hidden(company="meta", company_block=["meta"]) == ["company blocked"]
    assert hidden(applied=True) == ["already tracked"]
    assert hidden(applied_company=True, hide_applied_companies=True) == ["already applied to company"]


def test_location_time_pay_skills():
    assert hidden(location="Dublin, Ireland") == ["outside US"]
    assert hidden(location="Dublin, Ireland", us_only=False) == []
    assert hidden(work_modes=["remote"]) == ["work mode: onsite"]
    assert hidden(locations_include=["seattle"]) == ["location not in list"]
    assert hidden(age_days=10, posted_within_days=7) == ["older than 7d"]
    assert hidden(desc="$20/hr", min_hourly=30) == ["pay $20/hr"]
    assert hidden(desc="This is an unpaid role") == ["unpaid"]
    assert hidden(skills_required=["pytorch"]) == ["missing required skills"]
    assert hidden(desc="Ruby on Rails", skills_excluded=["ruby"]) == ["has excluded skill"]


def test_settings_reject_unknown_keys_but_file_loading_tolerates_them(tmp_path, monkeypatch):
    from backend import settings as settings_mod
    with pytest.raises(Exception):
        Settings(not_a_filter=True)  # the API rejects typos
    f = tmp_path / "settings.yaml"
    f.write_text("posted_within_days: 3\nold_removed_setting: 1\n")
    monkeypatch.setattr(settings_mod, "SETTINGS", f)
    assert settings_mod.get().posted_within_days == 3


# --- ranking & sources ----------------------------------------------------

def test_freshness_halves():
    assert abs(ranking.freshness(NOW - timedelta(days=5), NOW, 5) - 0.5) < 1e-9


def test_score_normalizes_weights_boost_and_personal():
    w = {"relevance": 1, "freshness": 1, "friendliness": 0, "personal": 2}
    total, parts = ranking.score(1, 1, 0.4, w)  # personal not trained yet: its weight is ignored
    assert total == 100 and parts == {"relevance": 50, "freshness": 50, "friendliness": 0}
    assert ranking.score(1, 1, 0.4, w, boost=10)[0] == 110
    total, parts = ranking.score(1, 1, 0.4, w, personal=0.5)
    assert total == 75 and parts["personal"] == 25


def test_greenhouse_parse():
    payload = {"jobs": [{"id": 1, "title": "Intern", "location": {"name": "NYC"}, "absolute_url": "u",
                         "content": "&lt;p&gt;Hello &amp;amp; CPT&lt;/p&gt;", "first_published": "2026-10-19T10:00:00Z"}]}
    (j,) = greenhouse.parse("acme", payload)
    assert j.id == "greenhouse:acme:1" and "CPT" in j.description and "<" not in j.description
