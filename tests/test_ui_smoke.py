"""Browser smoke test: a throwaway server on a scratch database, clicked through with Playwright.

Slow (~30 s, loads the embedding model). Skip with JOBPREP_SKIP_UI=1.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(bool(os.environ.get("JOBPREP_SKIP_UI")), reason="JOBPREP_SKIP_UI set")
playwright = pytest.importorskip("playwright.sync_api")

SEED = """
from datetime import datetime, timedelta, timezone
from backend.models import InboxItem, Job, init_db, session
init_db()
from backend import resume
resume.add("AI/ML", "resume.txt", (("M.S. computer science student. Machine learning, Python, PyTorch, LLM evaluation, "
           "retrieval-augmented generation, FastAPI, SQL, Docker. ") * 4).encode(), ["machine learning", "data", "ml"])
now = datetime.now(timezone.utc)
with session() as s:
    s.add(Job(id="greenhouse:acme:1", source="greenhouse", company="Acme AI", title="Machine Learning Intern (Summer 2027)",
              location="San Francisco, CA", url="https://example.com/1", posted_at=now - timedelta(hours=5),
              description="Build LLM evaluation pipelines with Python and PyTorch. CPT students welcome. Apply by December 1, 2026."))
    s.add(Job(id="lever:beta:2", source="lever", company="Beta Data", title="Data Science Intern", location="New York, NY",
              url="https://example.com/2", posted_at=now - timedelta(days=2), description="SQL, pandas, statistics."))
    s.add(Job(id="ashby:gamma:3", source="ashby", company="Gamma", title="Software Engineering Intern", location="Austin, TX",
              url="https://example.com/3", posted_at=now - timedelta(days=1), description="Must be a U.S. citizen. Java, React."))
    s.add(InboxItem(message_id="<m1@mail>", received_at=now, sender="Workday at Acme AI <acme@myworkday.com>",
                    subject="Thank you for your interest in Acme AI", kind="rejected",
                    snippet="Thank you for your application for the Machine Learning Intern (Summer 2027) position. Unfortunately..."))
    s.commit()
"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("jobprep")
    (tmp / "data").mkdir()
    env = {**os.environ, "JOBPREP_DATA": str(tmp / "data"), "JOBPREP_DB": str(tmp / "jobs.db"), "JOBPREP_NO_SCHEDULER": "1"}
    if (ROOT / "data" / "geo" / "cities.json").exists():  # reuse the city list instead of downloading it again
        (tmp / "data" / "geo").mkdir()
        shutil.copy(ROOT / "data" / "geo" / "cities.json", tmp / "data" / "geo" / "cities.json")
    subprocess.run([sys.executable, "-c", SEED], cwd=ROOT, env=env, check=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{base}/settings", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    yield base
    proc.terminate()
    proc.wait(10)
    shutil.rmtree(tmp, ignore_errors=True)


def test_ui_end_to_end(server):
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))

        page.goto(f"{server}/#home")
        page.wait_for_selector("#page-home >> text=Today’s picks", timeout=120000)

        # Jobs: ranked list, detail with score breakdown, eligibility, resume match
        page.click("#nav [data-page=jobs]")
        page.wait_for_selector("#list .job", timeout=120000)
        titles = page.locator("#list .job .t").all_inner_texts()
        assert any("Machine Learning Intern" in t for t in titles)
        assert not any("Software Engineering Intern" in t for t in titles)  # citizenship required -> hidden
        page.locator("#list .job", has_text="Machine Learning Intern").click()
        page.wait_for_selector("#detail >> text=Why ")
        page.wait_for_selector("#detail >> text=Tailor resume")
        glance = page.inner_text("#detail .glance")
        assert "CPT friendly" in glance and "Dec 1" in glance  # at-a-glance chips: CPT + deadline (shown as a date, not shifted a day)

        # show hidden explains why
        page.check("#showHidden")
        page.wait_for_selector("#list .job.is-hidden")
        assert "citizenship" in page.inner_text("#list")
        page.uncheck("#showHidden")

        # quick filter bar persists; drawer filters show an "also filtering by" chip that resets
        page.locator("[data-q=posted]").select_option("1")
        page.wait_for_timeout(1500)  # filter saves are debounced
        assert httpx.get(f"{server}/settings").json()["posted_within_days"] == 1
        page.locator("[data-q=posted]").select_option("0")
        page.click("#moreFilters")
        page.locator("#filters input[data-k=hide_expired]").uncheck()
        page.wait_for_selector("#activeFilters .tag")
        page.locator("#activeFilters [data-reset]").first.click()
        page.wait_for_timeout(1500)
        assert httpx.get(f"{server}/settings").json()["hide_expired"] is True
        page.click("#drawerClose")

        # location radius: near LA within 500 mi keeps San Francisco (~347 mi), hides New York; bad city is refused
        near = page.locator("[data-q=near]")
        near.fill("Los Angeles, CA")
        near.dispatch_event("change")
        page.wait_for_selector("[data-q=radius]:not([disabled])")
        page.locator("[data-q=radius]").select_option("500")
        page.wait_for_timeout(1500)
        page.wait_for_selector("#list .job >> text=mi")
        listing = page.inner_text("#list")
        assert "Machine Learning Intern" in listing and "347 mi" in listing and "Data Science Intern" not in listing
        near.fill("Qwertyvillezz")
        near.dispatch_event("change")
        page.wait_for_selector("text=find “Qwertyvillezz”")
        page.wait_for_timeout(800)
        assert httpx.get(f"{server}/settings").json()["near_location"] == "Los Angeles, CA"
        page.locator("[data-q=near]").fill("")
        page.locator("[data-q=near]").dispatch_event("change")
        page.wait_for_timeout(1500)

        # save -> tracker board card
        page.wait_for_selector("#list .job")
        page.locator("#list .job", has_text="Machine Learning Intern").click()
        page.click("#detail [data-act=saved]")
        page.click("#nav [data-page=tracker]")
        page.wait_for_selector(".tcard", timeout=30000)
        assert "Acme AI" in page.inner_text(".col[data-col=saved]")

        # resumes: upload a new version -> re-ranked, impact report, old version kept
        page.click("#nav [data-page=resumes]")
        page.wait_for_selector("#page-resumes >> text=Skills found")
        new = tmp_resume = Path(__file__).parent / "_tmp_resume.txt"
        tmp_resume.write_text(("Data analyst: SQL, pandas, statistics, Tableau, A/B testing, dashboards. ") * 6)
        try:
            page.set_input_files("#page-resumes input[data-replace]", str(new))
            page.wait_for_selector("#page-resumes >> text=What changed in your top", timeout=120000)
        finally:
            tmp_resume.unlink()
        assert "Earlier versions (1)" in page.inner_text("#page-resumes") and "pandas" in page.inner_text("#page-resumes")

        # tailor: paste a job description -> analysis; import Claude's reply -> accept -> score goes up; export is text
        page.click("#nav [data-page=tailor]")
        page.click("#tadd")
        page.wait_for_selector("#tpaste")
        jd = ("Responsibilities: Build machine learning pipelines in Python and PyTorch, and ship REST APIs with FastAPI. "
              "Minimum qualifications: Pursuing a degree in Computer Science. Experience with SQL and Docker. "
              "Strong communication and collaboration skills. Preferred qualifications: Experience with Kubernetes and Kafka.")
        page.fill("#tpaste [name=company]", "Kappa Labs")
        page.fill("#tpaste [name=title]", "Machine Learning Engineer Intern")
        page.fill("#tpaste [name=jd]", jd)
        page.click("#tpaste button")
        page.wait_for_selector("#tdetail >> text=match now", timeout=120000)
        assert "Kubernetes" in page.inner_text("#tdetail") and "Requirements" in page.inner_text("#tdetail")
        item = httpx.get(f"{server}/tailor").json()["items"][0]
        assert "truthful" in httpx.get(f"{server}/tailor/{item['id']}/prompt").json()["prompt"]
        reply = '{"suggestions": [{"type": "new", "target": "", "text": "Containerized PyTorch model services with Docker and Kubernetes for repeatable deploys.", "keywords": ["Kubernetes"], "why": "preferred"}], "skills_to_add": [], "summary": "", "gaps": ["Kafka"]}'
        page.click("#tdetail .tabs [data-tab=suggest]")
        page.fill("#treply", reply)
        page.click("#timport")
        page.wait_for_selector("#tdetail >> text=Review (")
        page.click("#tdetail [data-dec=accepted]")
        page.wait_for_selector("#tdetail >> text=with accepted bullets")
        after = httpx.get(f"{server}/tailor").json()["items"][0]
        assert after["with_accepted"] > after["score"] and after["accepted"] == 1
        export = httpx.get(f"{server}/tailor/{item['id']}/export").text
        assert "ADD: Containerized PyTorch" in export and "%PDF" not in export

        # inbox: role + company read from the email, one-click best match moves it into the tracker
        page.click("#nav [data-page=inbox]")
        page.wait_for_selector("#page-inbox >> text=Connect Gmail (read-only, free)")
        card = page.inner_text("#page-inbox")
        assert "Machine Learning Intern (Summer 2027)" in card and "Acme AI" in card
        page.locator("#page-inbox [data-attach]").first.click()
        page.wait_for_selector("#page-inbox >> text=In tracker (1)")
        apps = httpx.get(f"{server}/applications").json()
        assert any(a["company"] == "Acme AI" and a["status"] == "rejected" for a in apps)
        page.click("#nav [data-page=settings]")
        page.wait_for_selector("#page-settings >> text=Run at login")
        assert page.input_value("#page-settings input[data-k=daily_time]") == "06:00"
        page.fill("#page-settings input[data-k=daily_time]", "07:30")
        page.locator("#page-settings input[data-k=daily_time]").dispatch_event("change")
        page.wait_for_timeout(800)
        assert httpx.get(f"{server}/settings").json()["daily_time"] == "07:30"

        # phone width: no horizontal scroll
        page.set_viewport_size({"width": 390, "height": 844})
        page.click("#nav [data-page=jobs]")
        page.wait_for_selector("#list .job")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

        assert [e for e in errors if "status of 422" not in e] == []  # the 422 is the refused city above
        browser.close()
