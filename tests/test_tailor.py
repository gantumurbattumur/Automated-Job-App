import json

import pytest

from backend import tailor
from tests.test_sources_feed import db, fake_embeddings  # noqa: F401  (fixtures)

RESUME = """GANA B
SUMMARY
Software engineer and M.S. Computer Science student building AI systems.
SKILLS
Languages: Python, TypeScript/JavaScript, C++, SQL
Backend: FastAPI, PostgreSQL, Docker
EXPERIENCE
Software Engineer November 2024 - May 2026
Pinecone Academy Seattle, WA
● Built a full-stack platform using Next.js, Python/FastAPI, and PostgreSQL, supporting 500+ students with class
management and personalized learning workflows.
● Deployed services using Docker and AWS, collaborating with instructors on new features.
PROJECTS
Profile RAG | Github Link
● Engineered a Retrieval-Augmented Generation system using GPT-4 and FAISS, with
FastAPI endpoints that answer recruiter questions.
EDUCATION
Bachelor of Science in Computer Science, Whitworth University
Master of Science in Computer Science, CSU Dominguez Hills
"""

JD = ("About us: We build tools. Responsibilities: Build machine learning services in Python and PyTorch and expose REST APIs. "
      "Collaborate with cross-functional teams to ship features. Minimum Qualifications: Currently pursuing a Bachelor's or "
      "Master's degree in Computer Science or related field. Experience with SQL databases and Docker. Strong communication skills. "
      "Preferred Qualifications: Familiarity with tools such as Kubernetes, Kafka, or Terraform. "
      "Benefits: medical, dental and 401(k). We are an equal opportunity employer.")


def test_parse_resume_sections_entries_and_wrapped_bullets():
    r = tailor.parse_resume(RESUME)
    assert r["summary"].startswith("Software engineer") and len(r["skills"]) == 2
    assert [e["id"] for e in r["entries"]] == ["E1", "E2"]
    assert r["entries"][0]["header"].startswith("Software Engineer") and "Pinecone Academy" in r["entries"][0]["header"]
    assert len(r["entries"][0]["bullets"]) == 2 and r["entries"][0]["bullets"][0]["text"].endswith("learning workflows.")
    assert r["entries"][1]["bullets"][0]["text"].endswith("answer recruiter questions.")  # wrapped line starting "FastAPI"
    assert "Whitworth" in r["education"]


def test_parse_jd_sections_keywords_and_boilerplate():
    jd = tailor.parse_jd("Machine Learning Engineer Intern (Summer 2027)", JD)
    assert jd["title_core"] == "machine learning engineer"
    assert {"required", "preferred", "responsibilities"} <= set(jd["sections_found"])
    terms = {k["term"].lower() for k in jd["keywords"]}
    assert {"python", "pytorch", "sql", "docker", "kubernetes", "kafka", "terraform"} <= terms
    assert not {"ci", "cd", "us"} & terms
    assert jd["education"]["level"] == "bachelor" and "computer science" in jd["education"]["fields"]
    assert {"communication", "collaboration"} <= set(jd["soft_skills"])
    assert not any("medical" in r["text"] or "equal opportunity" in r["text"] for r in jd["requirements"])
    required = next(k for k in jd["keywords"] if k["key"] == "docker")
    preferred = next(k for k in jd["keywords"] if k["key"] == "kafka")
    assert required["term"] == "Docker"  # shown the way the job writes it
    assert required["weight"] > preferred["weight"]  # required qualifications count more


def test_analyze_scores_and_evidence(db, monkeypatch):  # noqa: F811
    fake_embeddings(monkeypatch, {"ai_ml": RESUME})
    jd = tailor.parse_jd("Machine Learning Engineer Intern", JD)
    a = tailor.analyze(jd, tailor.parse_resume(RESUME), other_resume_text="Kubernetes clusters")
    missing = {k["term"].lower() for k in a["keywords"] if not k["in_resume"]}
    assert {"pytorch", "kubernetes", "kafka"} <= missing and "python" not in missing
    assert next(k for k in a["keywords"] if k["key"] == "kubernetes")["on_other_resume"]
    assert a["parts"]["education"] == 100 and 0 < a["score"] < 100
    edu = next(r for r in a["requirements"] if "degree" in r["text"])
    assert edu["kind"] == "education" and edu["status"] == "met"
    assert {s["skill"]: s["in_resume"] for s in a["soft_skills"]}["collaboration"]  # "collaborating" counts


def test_suggestions_parse_apply_and_export(db, monkeypatch):  # noqa: F811
    fake_embeddings(monkeypatch, {"ai_ml": RESUME})
    reply = "Sure!\n```json\n" + json.dumps({"suggestions": [
        {"type": "rewrite", "target": "E1.2", "text": "Deployed PyTorch model services using Docker and AWS, collaborating with instructors on features.", "keywords": ["PyTorch"]},
        {"type": "new", "target": "E2", "text": "Exposed REST APIs for retrieval with FastAPI.", "keywords": ["REST"]},
        {"type": "new", "target": "E1", "text": ""}],
        "skills_to_add": ["PyTorch"], "summary": "ML engineer.", "gaps": ["Kafka"]}) + "\n```"
    d = tailor.parse_suggestions(reply)
    assert [s["id"] for s in d["suggestions"]] == ["s1", "s2"] and d["gaps"] == ["Kafka"]  # empty one dropped
    base = tailor.parse_resume(RESUME)
    d["suggestions"][0]["status"] = "accepted"
    applied = tailor.apply_suggestions(base, d, ("accepted",))
    assert "PyTorch" in applied["entries"][0]["bullets"][1]["text"] and len(applied["entries"][1]["bullets"]) == 1
    everything = tailor.apply_suggestions(base, {**d, "skills_status": "pending"}, ("accepted", "pending"))
    assert len(everything["entries"][1]["bullets"]) == 2 and "Additional: PyTorch" in everything["skills"]
    jd = tailor.parse_jd("Machine Learning Engineer Intern", JD)
    assert tailor.analyze(jd, applied)["score"] > tailor.analyze(jd, base)["score"]
    text = tailor.export_text({"title": "MLE Intern", "company": "Kappa"}, base, d)
    rewrite_only_drops = tailor.parse_suggestions(json.dumps({"suggestions": [{"type": "rewrite", "target": "E1.2", "text": "Deployed PyTorch services."}]}))
    rewrite_only_drops["suggestions"][0]["status"] = "accepted"
    assert tailor.analyze(jd, tailor.apply_suggestions(base, rewrite_only_drops))["parts"]["soft_skills"] < tailor.analyze(jd, base)["parts"]["soft_skills"]
    assert "REPLACE: Deployed services using Docker" in text and "WITH: Deployed PyTorch" in text and "Exposed REST" not in text
    assert tailor.parse_suggestions("- Built a thing that matters a lot\n- Shipped another thing to users")["suggestions"][1]["type"] == "new"


def test_list_limit_and_validation(db, monkeypatch):  # noqa: F811
    from datetime import datetime, timezone
    from backend import models
    from backend.models import Job
    with models.session() as s:
        s.add(Job(id="j1", source="t", company="Acme", title="ML Intern", url="u", posted_at=datetime.now(timezone.utc), description=JD))
        s.add(Job(id="j2", source="t", company="Acme", title="Short", url="u", posted_at=datetime.now(timezone.utc), description="tiny"))
        s.commit()
    assert tailor.add("j1") == "j1" and tailor.add("j1") == "j1"  # adding twice is a no-op
    with pytest.raises(ValueError, match="only has this posting"):
        tailor.add("j2")
    with pytest.raises(ValueError, match="Paste the full"):
        tailor.add(company="X", title="Y", jd_text="too short")
    for i in range(9):
        tailor.add(company=f"C{i}", title="Intern", jd_text=JD)
    with pytest.raises(ValueError, match="up to 10"):
        tailor.add(company="One more", title="Intern", jd_text=JD)
    assert tailor.items()[1].id == "paste:1"


def test_resume_without_bullets_scores_requirements_as_missing(db, monkeypatch):  # noqa: F811
    fake_embeddings(monkeypatch, {"ai_ml": "x"})
    jd = tailor.parse_jd("ML Intern", JD)
    bare = tailor.parse_resume("SKILLS\nPython, SQL")
    a = tailor.analyze(jd, bare)
    assert a["parts"]["requirements"] < 50 and any(r["status"] == "missing" for r in a["requirements"])


def test_same_posting_from_two_sources_is_one_entry(db):  # noqa: F811
    from datetime import datetime, timezone
    from backend import models
    from backend.models import Job
    with models.session() as s:
        for jid, src in (("simplify:1", "simplify"), ("workday:t:R1", "workday")):
            s.add(Job(id=jid, source=src, company="Trimble", title="ML Engineer Intern", url=jid, posted_at=datetime.now(timezone.utc), description=JD))
        s.commit()
    assert tailor.add("simplify:1") == "simplify:1" and tailor.add("workday:t:R1") == "simplify:1"
    assert len(tailor.items()) == 1


def test_culture_lines_are_not_requirements_and_slash_titles():
    jd = tailor.parse_jd("AI/ML Developer Intern", "We make bold moves, together. We know varied backgrounds are valued. "
                         "Follow @Careers on Instagram #interns. Experience with Python and PyTorch for model training. "
                         "Ability to communicate results to engineers.")
    texts = [r["text"] for r in jd["requirements"]]
    assert texts == ["Experience with Python and PyTorch for model training.", "Ability to communicate results to engineers."]
    a = tailor.analyze(jd, tailor.parse_resume("SUMMARY\nAI engineer building ML systems."))
    assert "ai/ml" in a["title"]["hits"]


def test_run_together_lists_and_more_headers():
    jd = tailor.parse_jd("AI/ML Developer Intern", "This role is Hybrid and you will work on average 2 days per week from an HPE office. "
        "Responsibilities: Collaborate with teams to design ML solutions Design, implement, and validate machine learning algorithms on big data "
        "Deploy AI/ML systems using CI/CD pipelines Education & Experience required: Working towards a Master's degree in Computer Science "
        "Proficient Python coder (PySpark, Scikit-learn) Strong background in statistics and deep learning "
        "What We Can Offer You: Health & Wellbeing benefits for your loved ones.")
    texts = [r["text"] for r in jd["requirements"]]
    assert len(texts) == 6 and any(t.startswith("Design, implement") for t in texts)
    assert any(t.startswith("Proficient Python") for t in texts) and any(t.startswith("Working towards a Master") for t in texts)
    assert not any("days per week" in t or "Wellbeing" in t for t in texts)
    assert {"required", "responsibilities"} <= set(jd["sections_found"])


def test_resume_map_places_missing_keywords_where_they_fit(monkeypatch):
    import numpy as np
    vocab = ["docker", "deploy", "service", "rag", "gpt", "faiss", "stream", "event"]

    def embed(texts):  # bag of words: "deploy services with Kubernetes" reads like the Docker/AWS bullet
        v = np.array([[t.lower().count(w) for w in vocab] for t in texts], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v, axis=1, keepdims=True)
    monkeypatch.setattr(tailor, "_embed", embed)
    jd = tailor.parse_jd("Software Engineer Intern", "Minimum Qualifications: Experience to deploy services with Docker and "
                         "Kubernetes. Experience to stream events with Kafka. Must be authorized to work in the United States "
                         "without sponsorship. Benefits: medical.", company="Kubeflow Systems")
    r = tailor.parse_resume(RESUME)
    m = tailor.resume_map(jd, r, tailor.analyze(jd, r))
    bullets = {b["id"]: b for e in m["entries"] for b in e["bullets"]}
    assert [x["term"] for x in bullets["E1.2"]["adds"]] == ["Kubernetes"]  # fits the "Deployed services using Docker" bullet
    assert "Kubernetes" in bullets["E1.2"]["adds"][0]["why"]  # with the job's own sentence as the reason
    assert [g["term"] for g in m["gaps"]] == ["Kafka"]  # nothing supports it: an honest gap, not a placement
    assert any("Kafka" in [x["term"] for x in line["adds"]] for line in m["skills"])  # tools also go on a skills line
    docker = bullets["E1.2"]["text"].lower().index("docker")
    assert [docker, docker + 6] in bullets["E1.2"]["hl"]  # matched keywords are highlighted in place
    assert not any("authorized" in u for u in m["unshown_requirements"])  # eligibility lines aren't resume content


def test_parse_jd_skips_company_names_and_intro_products():
    jd = tailor.parse_jd("Robotics Intern", "GrayMatter Robotics builds GMR SuperCell for factories. "
                         "Requirements: Experience with Python and ROS. Familiarity with GrayMatter tools.", company="GrayMatter Robotics")
    terms = {k["term"] for k in jd["keywords"]}
    assert {"Python", "ROS"} <= terms and not {"GrayMatter", "SuperCell", "GMR"} & terms


def test_generate_runs_claude_code_on_your_plan_not_the_api(monkeypatch, tmp_path):
    """One click: the prompt goes to the local `claude` CLI; API keys are stripped so it can only use your plan."""
    fake = tmp_path / "claude"
    fake.write_text('#!/bin/sh\nprompt=$(cat)\nkey=${ANTHROPIC_API_KEY:-none}\n'
                    'printf \'{"is_error": false, "result": "{\\\\"suggestions\\\\": [], \\\\"gaps\\\\": [\\\\"%s|%s|%s\\\\"]}"}\' '
                    '"$prompt" "$key" "$*"\n')
    fake.chmod(0o755)
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")
    monkeypatch.setattr(tailor, "claude_cli", lambda: str(fake))
    monkeypatch.setattr(tailor, "prompt", lambda item_id: "THE PROMPT")
    monkeypatch.setattr(tailor, "import_suggestions", lambda item_id, raw: seen.update(id=item_id, raw=raw))
    tailor._generate("job1", "sonnet")
    assert tailor._runs["job1"] == {"state": "done"} and seen["id"] == "job1"
    prompt, key, args = json.loads(seen["raw"])["gaps"][0].split("|")
    assert prompt == "THE PROMPT" and key == "none"  # no API key reaches Claude Code
    assert "--tools" in args and "--model sonnet" in args and "-p" in args

    fake.write_text('#!/bin/sh\necho \'{"is_error": true, "result": "Usage limit reached"}\'\nexit 1\n')
    tailor._generate("job1", "sonnet")
    assert tailor._runs["job1"]["state"] == "error" and "Usage limit" in tailor._runs["job1"]["error"]


def test_tailored_text_is_the_whole_resume_with_changes():
    r = tailor.parse_resume(RESUME)
    d = {"suggestions": [{"id": "s1", "type": "rewrite", "target": "E1.2", "text": "Deployed PyTorch services with Docker and AWS.",
                          "status": "accepted"}]}
    text = tailor.tailored_text(tailor.apply_suggestions(r, d))
    assert text.index("SUMMARY") < text.index("SKILLS") < text.index("EXPERIENCE") < text.index("PROJECTS") < text.index("EDUCATION")
    assert "● Deployed PyTorch services with Docker and AWS." in text and "Deployed services using Docker and AWS, collab" not in text
    assert "Profile RAG" in text and "Whitworth" in text
