"""Fill a job application in a visible browser from your profile + canned answers. It NEVER submits.

Run by the app as: python -m backend.autofill <job_id>
The browser stays open for you to review highlighted fields and click Submit yourself; when the page shows a
confirmation ("thank you for applying"…), the tracker is marked applied.
"""
import re
import sys
import time

from .config import DATA, ROOT, load
from .models import Job, init_db, session

CONFIRM = re.compile(r"thank you for (applying|your application)|application (has been |was )?(submitted|received)|"
                     r"we('ve| have) received your application|thanks for applying", re.I)

# label regex -> profile attribute (first match wins); checked before canned answers
FIELDS = [
    (r"preferred", "preferred_name"),
    (r"first\s*name|given\s*name", "first_name"),
    (r"last\s*name|family\s*name|surname", "last_name"),
    (r"^\s*(full\s*|legal\s*)?name\s*\*?\s*$", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url)", "website"),
    (r"^\s*country\s*\*?\s*$", "country"),
    (r"location|city|where are you (currently )?(based|located)|current address|based in", "location"),
    (r"school|university|college|institution", "school"),
    (r"^\s*degree\s*\*?\s*$|degree type|level of education", "degree"),
    (r"discipline|major|field of study|area of study|course (are you studying|of study)|what are you studying", "major"),
    (r"\bgpa\b|grade point", "gpa"),
    (r"end (date )?year|graduation (year|date)|expected graduation|grad(uation)? date", "grad_year"),
]

# Finds every visible, fillable control, tags it with data-jp, and returns its best label text.
SCAN_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const text = e => (e?.innerText || e?.textContent || '').replace(/\s+/g, ' ').trim();
  const labelOf = e => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return text(l); }
    const by = e.getAttribute('aria-labelledby');
    if (by) { const t = by.split(' ').map(i => text(document.getElementById(i))).join(' '); if (t) return t; }
    const l = e.closest('label'); if (l && text(l)) return text(l);
    const fs = e.closest('fieldset'); if (fs && fs.querySelector('legend')) return text(fs.querySelector('legend'));
    let c = e.parentElement;
    for (let i = 0; i < 4 && c; i++, c = c.parentElement) {
      const l2 = c.querySelector('label, legend, [class*=label], [class*=question]');
      if (l2 && !l2.contains(e) && text(l2)) return text(l2);
    }
    return e.getAttribute('aria-label') || e.placeholder || e.name || '';
  };
  const out = []; let i = 0;
  document.querySelectorAll('input, select, textarea').forEach(e => {
    const t = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(t)) return;
    if (e.name === 'g-recaptcha-response' || (t !== 'file' && !vis(e)) || e.disabled || e.readOnly) return;
    if (t !== 'file' && (e.tabIndex === -1 || e.getAttribute('aria-hidden') === 'true')) return;  // React's hidden twins
    if (e.closest('.iti__country-container, .iti__dropdown-content, [class*=country-select]')) return;  // phone flag picker
    e.setAttribute('data-jp', String(i));
    out.push({ i: i++, tag: e.tagName.toLowerCase(), type: t, label: labelOf(e).slice(0, 300), id: e.id, name: e.name,
      required: e.required || e.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(labelOf(e)),
      combo: e.getAttribute('role') === 'combobox' || !!e.getAttribute('aria-autocomplete'),
      group: (t === 'radio' || t === 'checkbox') ? (e.closest('fieldset, [role=radiogroup], [role=group]') ? labelOf(e.closest('fieldset, [role=radiogroup], [role=group]').querySelector('input') || e) : '') : '',
      optionText: (t === 'radio' || t === 'checkbox') ? text(e.closest('label') || document.querySelector(`label[for="${CSS.escape(e.id || '_')}"]`)) : '',
      value: e.value || '', checked: !!e.checked,
      options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()) : [] });
  });
  return out;
}"""

BANNER_JS = """([filled, missing]) => {
  document.getElementById('jobprep-banner')?.remove();
  const d = document.createElement('div'); d.id = 'jobprep-banner';
  d.style.cssText = 'position:fixed;z-index:2147483647;bottom:16px;right:16px;max-width:360px;background:#1d2026;color:#fff;' +
    'font:14px system-ui;padding:12px 14px;border-radius:10px;box-shadow:0 4px 20px #0006';
  d.innerHTML = `<b>JobPrep filled ${filled} field(s).</b><br>` +
    (missing.length ? `Check the <span style="color:#f87171">red</span> fields (${missing.length}): ` +
      missing.slice(0, 6).map(m => m.replace(/[<>&]/g, '')).join('; ') : 'Nothing left that I could see.') +
    '<br><br>Review everything, then click <b>Submit</b> yourself. JobPrep never submits.' +
    '<br><a href="#" style="color:#6ea0ff" onclick="this.parentElement.remove();return false">dismiss</a>';
  document.body.appendChild(d);
}"""


def _apply_url(url: str) -> str:
    if "jobs.lever.co" in url and not url.rstrip("/").endswith("/apply"):
        return url.rstrip("/") + "/apply"
    if "jobs.ashbyhq.com" in url and "/application" not in url:
        return url.rstrip("/") + "/application"
    return url


def values(profile) -> dict[str, str]:
    year = re.search(r"(20\d\d)", profile.graduation or "")
    return {**profile.model_dump(), "full_name": f"{profile.first_name} {profile.last_name}".strip(),
            "country": "United States", "grad_year": year[1] if year else ""}


def answer_for(label: str, company: str, profile, vals: dict) -> str | None:
    """Value to put in a field with this label, or None if we don't know (it gets highlighted)."""
    from .profile import match_answer
    clean = re.sub(r"\s*[*✱]+\s*$", "", re.sub(r"\s+", " ", label)).strip()  # "Full name ✱" -> "Full name"
    a = match_answer(clean, company, profile)  # specific questions ("…require sponsorship…") beat generic field names
    if a and a["answer"]:
        return a["answer"]
    for pattern, key in FIELDS:
        if re.search(pattern, clean, re.I) and vals.get(key):
            return vals[key]
    return None


STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
          "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
          "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
          "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
          "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
          "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
          "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
          "DC": "District of Columbia"}


DECLINE = re.compile(r"decline|prefer not|don.?t (wish|want)|do not (wish|want)|not to (answer|disclose|say)", re.I)


def _tokens(s: str) -> set[str]:
    s = re.sub(r"'s\b", "", s.lower())
    return {t for t in re.split(r"[^a-z0-9]+", s) if t and t not in {"of", "the", "and", "in", "at"}}


def _expand(answer: str) -> str:
    """Spell out what option lists spell out: "Carson, CA" -> "Carson, California"; "Master of Science" -> + "Master's Degree"."""
    a = re.sub(r",\s*([A-Z]{2})\b", lambda m: f", {STATES.get(m[1], m[1])}", answer)
    if re.search(r"\bmaster|\bm\.?s\.?\b", a, re.I) and "degree" not in a.lower():
        a += " master's degree"
    if re.search(r"\bbachelor|\bb\.?s\.?\b", a, re.I) and "degree" not in a.lower():
        a += " bachelor's degree"
    return a


def _pick(options: list[str], answer: str) -> str | None:
    """Exact (ignoring punctuation) > prefix > best token overlap. None if nothing is a convincing match."""
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())  # noqa: E731
    a = norm(answer)
    if not a:
        return None
    if DECLINE.search(answer):
        # every form words "I won't say" differently; any such option is the same answer
        for o in options:
            if re.search(r"decline|prefer not|don.?t (wish|want)|not to (answer|disclose|say|self)|choose not|rather not", o, re.I):
                return o
    for o in options:
        if norm(o) == a:
            return o
    for o in options:
        if norm(o).startswith(a):
            return o
    want = _tokens(_expand(answer))
    best, best_score = None, 0.0
    for o in options:
        have = _tokens(o)
        if not have:
            continue
        hit = len(want & have)
        score = hit / len(have) if hit else 0  # how much of the option is explained by the answer
        if score > best_score:
            best, best_score = o, score
    return best if best_score >= 0.5 else None


def _query(label: str, answer: str) -> str:
    """What to type into a searchable dropdown: the distinctive start of the answer."""
    first = re.split(r"[,(\-–]", answer)[0].strip()
    if re.search(r"degree", label, re.I):
        return first.split()[0]  # "Master", "Bachelor"
    return first[:40] or answer[:40]


def fill(page, company: str, profile, resume_path: str) -> tuple[int, list[str]]:
    vals = values(profile)
    filled, missing = 0, []
    done_groups = set()
    for f in page.evaluate(SCAN_JS):
        el = page.locator(f'[data-jp="{f["i"]}"]')
        label = f["label"]
        try:
            if f["type"] == "file":
                if re.search(r"resume|cv\b", f"{label} {f['id']} {f['name']}", re.I) or (f["required"] and "cover" not in label.lower()):
                    el.set_input_files(resume_path)
                    filled += 1
                continue
            if f["type"] in ("radio", "checkbox"):
                group = f["group"] or label
                if group in done_groups:
                    continue
                ans = answer_for(group, company, profile, vals)
                if not ans:
                    if f["required"] and not f["checked"]:
                        missing.append(group[:80])
                    done_groups.add(group)
                    continue
                if _pick([f["optionText"]], ans):
                    el.check(force=True)
                    filled += 1
                    done_groups.add(group)
                continue
            if f["value"]:  # already filled (e.g. by resume parsing) - leave it alone
                continue
            ans = answer_for(label, company, profile, vals)
            if ans is None:
                if f["required"]:
                    missing.append(label[:80] or f["id"])
                    el.evaluate("e => e.style.outline = '2px solid #f87171'")
                continue
            if f["tag"] == "select":
                opt = _pick(f["options"], ans)
                if opt:
                    el.select_option(label=opt)
                    filled += 1
                else:
                    missing.append(label[:80])
                    el.evaluate("e => e.style.outline = '2px solid #f87171'")
            elif f["combo"]:  # React/autocomplete dropdown: type, then click the best matching option
                el.click()
                if not DECLINE.search(ans):  # "won't say" is worded differently everywhere: open the full list instead
                    el.press_sequentially(_query(label, ans), delay=15)
                page.wait_for_timeout(1500)
                ctl = el.get_attribute("aria-controls")  # its own listbox, not e.g. the phone-country list
                opts = page.locator(f'[id="{ctl}"] [role=option]') if ctl else page.locator("[role=option]:visible")
                texts = [t.strip() for t in opts.all_inner_texts()[:50]]
                opt = _pick(texts, ans)
                if opt:
                    opts.nth(texts.index(opt)).click()
                    filled += 1
                else:
                    el.press("Escape")
                    missing.append(label[:80])
                    el.evaluate("e => e.style.outline = '2px solid #f87171'")
            else:
                el.fill(ans)
                filled += 1
        except Exception:  # one odd widget shouldn't stop the rest
            missing.append(label[:80] or f["id"])
    return filled, missing


def main(job_id: str) -> None:
    from playwright.sync_api import sync_playwright
    from . import profile as profile_mod
    from . import tracker

    init_db()
    with session() as s:
        job = s.get(Job, job_id)
    if not job:
        sys.exit(f"unknown job {job_id}")
    p = profile_mod.get()
    from .feed import build
    from .settings import get as get_settings
    row = next((x for x in build(get_settings()) if x["id"] == job_id), {})
    lanes = load("resumes")["lanes"]
    lane = row.get("best_lane") if row.get("best_lane") in lanes else load("resumes")["primary"]
    resume_path = str(ROOT / lanes[lane])

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(DATA / "browser-profile"), headless=False,
                                                    viewport=None, args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_apply_url(job.url), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)  # let React forms render
        filled, missing = fill(page, job.company, p, resume_path)
        page.evaluate(BANNER_JS, [filled, missing])
        tracker.set_status(job_id, "opened", "autofill")
        print(f"filled {filled}, needs review: {missing}", flush=True)
        # wait for you: mark applied when a confirmation appears; exit when the browser is closed
        while ctx.pages:
            try:
                for pg in ctx.pages:
                    if CONFIRM.search(pg.inner_text("body", timeout=2000)[:5000]):
                        tracker.set_status(job_id, "applied", "autofill")
                        print("confirmation seen: marked applied", flush=True)
                        ctx.close()
                        return
            except Exception:
                pass
            time.sleep(2)


if __name__ == "__main__":
    main(sys.argv[1])
