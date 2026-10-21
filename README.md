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
.venv/bin/python -m playwright install chromium   # only needed for Autofill
```

## Pages
- **Home** – today's top new matches, deadlines this week, pipeline, follow-ups, what the ranking learned about you, source health.
- **Jobs** – filters (left) · ranked list (middle, `j`/`k` to move) · details (right): why it ranks there, F-1/CPT eligibility,
  H-1B history, resume help (which resume, which bullets to lead with, skill gaps, a copyable Claude prompt) and the packet.
- **Tracker** – drag-and-drop board or table; tag "rejected over CPT" so that company ranks lower next time.
- **Inbox** – Gmail connection and job emails to review. **Resumes** – upload/replace/revert resumes (PDF, DOCX, TXT),
  set the primary one and title keywords; every job is re-scored immediately and you see how your top 50 changed.
  **Profile** – your info + canned answers. **Settings** – schedule, alerts, cleanup/backups, run-at-login.

Run in the background at login instead of a terminal: `scripts/install_service.sh` (undo: `scripts/uninstall_service.sh`).

## How it works
- **Sources**: SimplifyJobs Summer internship list (GitHub) + Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable boards.
  Boards are discovered automatically from the SimplifyJobs links; add more in `config/companies.yaml`.
  Only internship/co-op titles are stored. Postings that disappear are marked closed.
- **Ranking**: relevance (local embeddings of your resume vs. the posting + skills overlap + title) · freshness
  (halves every N days) · CPT-friendliness (posting wording, else H-1B approvals from USCIS data in `config/h1b.yaml`)
  · your taste (a small model trained on what you save/apply to vs. mark "not interested"; off until it has 5 of each).
  Weights are sliders in the Ranking filter group.
- **Apply ↗** opens the posting (Simplify's extension fills it in your Chrome); when you come back the app asks
  whether you applied. **Packet** shows copy-ready info/answers and the resume to upload.
  **Autofill** opens a separate browser that fills Greenhouse/Lever/Ashby-style forms and **never submits**.
- **Tracker**: statuses, notes, follow-up dates, CSV export; applications with no news for 30 days become "ghosted".
- **Inbox**: optional read-only Gmail scan (IMAP + Google app password stored in the macOS Keychain) that moves
  applications to applied / OA / interview / offer / rejected; unmatched emails wait for your review.

## Where things live
- `config/rules.yaml` – what phrases mean (CPT/citizenship wording, categories, locations)
- `config/skills.txt` – skills dictionary for matched / missing skills
- `config/companies.yaml` – extra job boards
- `data/` (gitignored, personal) – resumes (`resume/`, `resumes.yaml`), `profile.yaml` (your info + canned answers), `settings.yaml` (filters),
  `presets.yaml`, `gmail.yaml` (address only), `models/` (embedding model), `browser-profile/` (Autofill browser)

## Test
```
JOBPREP_NO_SCHEDULER=1 .venv/bin/python -m pytest -q          # includes a ~15 s browser test
JOBPREP_NO_SCHEDULER=1 JOBPREP_SKIP_UI=1 .venv/bin/python -m pytest -q   # skip it
```
