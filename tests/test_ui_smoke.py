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
        page.wait_for_selector("#page-home >> text=Today’s top matches", timeout=120000)

        # Jobs: ranked list, detail with score breakdown, eligibility, resume help, packet
        page.click("#nav [data-page=jobs]")
        page.wait_for_selector("#list .job", timeout=120000)
        titles = page.locator("#list .job .t").all_inner_texts()
        assert any("Machine Learning Intern" in t for t in titles)
        assert not any("Software Engineering Intern" in t for t in titles)  # citizenship required -> hidden
        page.locator("#list .job", has_text="Machine Learning Intern").click()
        page.wait_for_selector("text=Why it ranks here")
        page.wait_for_selector("#resumeHelp h4", timeout=60000)
        page.wait_for_selector("#packet table")
        elig = page.locator("#detail .card", has_text="Eligibility for you").inner_text()
        assert "CPT friendly" in elig and "Dec 1" in elig

        # show hidden explains why
        page.check("#showHidden")
        page.wait_for_selector("#list .job.is-hidden")
        assert "citizenship" in page.inner_text("#list")
        page.uncheck("#showHidden")

        # a filter change persists and shows an active-filter chip
        page.locator("#filters select[data-k=posted_within_days]").select_option("1")
        page.wait_for_selector("#activeFilters .tag")
        page.wait_for_timeout(1500)  # filter saves are debounced
        assert httpx.get(f"{server}/settings").json()["posted_within_days"] == 1
        page.locator("#activeFilters [data-reset]").first.click()
        page.wait_for_timeout(800)

        # save -> tracker board card
        page.wait_for_selector("#list .job")
        page.locator("#list .job", has_text="Machine Learning Intern").click()
        page.click("#detail [data-act=saved]")
        page.click("#nav [data-page=tracker]")
        page.wait_for_selector(".tcard", timeout=30000)
        assert "Acme AI" in page.inner_text(".col[data-col=saved]")

        # profile save round-trip
        page.click("#nav [data-page=profile]")
        page.wait_for_selector("#page-profile >> text=Undergraduate")
        page.fill("[data-p=graduation]", "May 2028")
        page.click("#pSave")
        page.wait_for_timeout(800)
        assert httpx.get(f"{server}/profile").json()["graduation"] == "May 2028"

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

        assert errors == []
        browser.close()
