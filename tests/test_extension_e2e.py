"""The Chrome extension, loaded into Chromium, filling a form served at a Greenhouse URL. Never submits.

Skip with JOBPREP_SKIP_UI=1 (shares the scratch server from test_ui_smoke).
"""
import json
from pathlib import Path

import httpx
import pytest

from tests.test_ui_smoke import pytestmark, server  # noqa: F401  (same skip rule + scratch server fixture)

ROOT = Path(__file__).resolve().parent.parent
FORM = (Path(__file__).parent / "fixtures" / "application_form.html").read_text()
PROFILE = {"first_name": "Test", "last_name": "Person", "email": "test@example.com", "phone": "5555550100",
           "linkedin": "https://linkedin.com/in/example", "school": "California State University, Dominguez Hills",
           "degree": "Master of Science", "major": "Computer Science", "graduation": "May 2028"}


def test_extension_fills_form(server, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright
    prof = httpx.get(f"{server}/profile").json()
    httpx.put(f"{server}/profile", json={**prof, **PROFILE}).raise_for_status()
    ext = str(ROOT / "extension")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(tmp_path / "chrome"), channel="chromium", headless=True,
                                                    args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        sw.evaluate(f"chrome.storage.local.set({{apiBase: {json.dumps(server)}}})")
        ctx.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(status=200, content_type="text/html", body=FORM))
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("https://job-boards.greenhouse.io/acme/jobs/999")
        page.locator("#jobprep-root .btn").click()
        page.wait_for_selector("#jobprep-root .panel.open h3", timeout=30000)
        panel = page.locator("#jobprep-root .panel").inner_text()

        v = lambda sel: page.input_value(sel)  # noqa: E731
        assert v("#first_name") == "Test" and v("#last_name") == "Person" and v("#email") == "test@example.com"
        assert v("#li") == PROFILE["linkedin"] and v("#school").startswith("California State")
        assert v("#degree") == "ms"  # "Master of Science" -> "Master's Degree", not the MBA
        assert page.evaluate("document.getElementById('resume').files[0].name").endswith(".txt")
        assert page.is_checked("input[name=spons][value=y]") and page.is_checked("input[name=auth][value=y]")
        assert page.inner_text("#gender") == "I do not wish to self-identify"  # "decline" matched a differently-worded option
        assert page.locator("button", has_text="Draft with Claude").count() == 1  # essay helper, essay left blank
        assert v("#why") == ""
        assert "Who referred you" in panel and "need you" in panel  # unknown required field flagged
        assert "2px" in page.evaluate("document.getElementById('ref').style.outline")
        assert not page.evaluate("!!window.submitted")  # never submits

        runs = httpx.get(f"{server}/ext/runs").json()
        assert runs[0]["status"] == "opened" and runs[0]["filled"] >= 10 and runs[0]["company"] == "Acme AI"
        page.locator("#jobprep-root [data-act=applied]").click()
        page.wait_for_timeout(500)
        assert any(a["company"] == "Acme AI" and a["status"] == "applied" for a in httpx.get(f"{server}/applications").json())
        assert errors == []
        ctx.close()
