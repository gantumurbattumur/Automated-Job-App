# JobPrep

Local, free internship finder for an F-1 (CPT) grad student. It pulls postings from free public sources, filters them
with your rules, ranks the newest + most relevant to your resume first, helps you apply, and tracks everything.
No paid APIs: matching runs a small local model (~64 MB) on your Mac.

## Run
```
.venv/bin/uvicorn backend.api:app --reload
```
Open http://127.0.0.1:8000. Refreshing runs on its own (SimplifyJobs list every 15 min, company boards hourly).

First-time setup if the venv is new:
```
uv pip install --python .venv/bin/python fastapi "uvicorn[standard]" sqlmodel httpx pyyaml pdfplumber fastembed keyring playwright openpyxl scikit-learn pytest
.venv/bin/python -m playwright install chromium   # only needed for the browser tests
```

## Pages
- **Home** – your plan for today: the best new picks (with the skills they match), to-dos (follow-ups, inbox, deadlines), tailoring progress, your application pipeline, a 7-day chart and recent activity. **Jobs** is where you browse and filter everything.
- **Jobs** – laid out like LinkedIn: search + "near <city> within N miles" (free offline city list), a row of filter
  pills (date posted, role, season, CPT, remote, **All filters**), a quiet list (company, location, age, "N skills match ·
  X% match", `j`/`k` to move) and the job on the right: Apply · Save · Tailor, **How you match** (skills you have and
  miss, why it ranked here, any eligibility warnings) and the description.
- **Tracker** – drag-and-drop board or table; tag "rejected over CPT" so that company ranks lower next time.
  The **Gmail inbox** tab holds job emails to review (the red count on Tracker).
- **Tailor** – match your resume to up to 10 jobs (below). The **Your resumes** tab uploads/replaces/reverts resumes
  (PDF, DOCX, TXT) and sets the primary one; every job is re-scored immediately and you see how your top 50 changed.
- **Settings** – schedule, alerts, tailoring model, cleanup/backups, run-at-login.

## Tailor your resume (up to 10 jobs)
For each job, JobPrep compares the description with your resume and scores the match like ATS tools do — hard-skill
keywords 40% (frequent / required ones weigh more), requirements backed by a bullet 25%, job title 15%, education 10%,
soft skills 10%. It shows every missing keyword, each requirement with your closest bullet, and builds a prompt.

**Resume map** (on the Tailor page): your resume laid out section by section, with the job's
keywords highlighted where you already have them, each missing keyword placed on the bullet or skills line it fits best
(hover a chip to see the job's sentence), which bullets prove which requirements, and the gaps nothing in your resume
supports — add those only if they're true.
1. **✎ Tailor resume** on a job (or paste any description on the Tailor page). Claude starts writing right away.
2. **✨ Write suggestions with Claude** runs Claude Code on this Mac with your own plan (`claude -p`, tools off, API
   keys removed from its environment so it can never bill the paid API); 10–60 s. Model: Settings → Tailoring
   (Sonnet by default). No Claude Code? "Or do it by hand" copies the prompt and takes the pasted reply, or run
   `/tailor-resume` in Claude Code.
3. **Review**: each suggestion shows before → after (click to edit), the keywords it adds, and anything it would drop.
   Accept, skip, or **Accept all**.
4. **Copy tailored resume** gives your whole resume as plain text with the accepted changes in place (or **Copy
   changes only**). Paste it into your own document — JobPrep never creates resume files — then upload the new
   version on the Resumes page.
Suggestions must be true: the prompt forbids invented experience and lists what you can't claim as "gaps".

Run in the background at login instead of a terminal: `scripts/install_service.sh` (undo: `scripts/uninstall_service.sh`).

## How it works
- **Sources**: SimplifyJobs Summer internship list (GitHub) + Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable boards.
  Boards are discovered automatically from the SimplifyJobs links; add more in `config/companies.yaml`.
  Only internship/co-op titles are stored. Postings that disappear are marked closed.
- **Ranking**: relevance (local embeddings of your resume vs. the posting + skills overlap + title) · freshness
  (halves every N days) · CPT-friendliness (posting wording, else H-1B approvals from USCIS data in `config/h1b.yaml`)
  · your taste (a small model trained on what you save/apply to vs. mark "not interested"; off until it has 5 of each).
  Weights are sliders in the Ranking filter group.
- **Apply ↗** opens the posting (use Simplify or similar to fill it); when you come back the app asks whether you applied.
- **Tracker**: statuses, notes, follow-up dates, CSV export; applications with no news for 30 days become "ghosted".
- **Inbox**: optional read-only Gmail scan (IMAP + Google app password stored in the macOS Keychain) that moves
  applications to applied / OA / interview / offer / rejected; unmatched emails wait for your review.

## Where things live
- `config/rules.yaml` – what phrases mean (CPT/citizenship wording, categories, locations)
- `config/skills.txt` – skills dictionary for matched / missing skills
- `config/companies.yaml` – extra job boards
- `data/` (gitignored, personal) – resumes (`resume/`, `resumes.yaml`), `settings.yaml` (filters),
  `presets.yaml`, `gmail.yaml` (address only), `models/` (embedding model), `geo/` (city list), `backups/`

## Test
```
JOBPREP_NO_SCHEDULER=1 .venv/bin/python -m pytest -q          # includes a ~15 s browser test
JOBPREP_NO_SCHEDULER=1 JOBPREP_SKIP_UI=1 .venv/bin/python -m pytest -q   # skip it
```
