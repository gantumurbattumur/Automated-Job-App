---
name: ship-update
description: Finish a major JobPrep change properly — verify it works, review the diff, write a descriptive commit message, and commit so the project history stays complete. Use after any significant feature, fix batch, refactor or UI change in this repo, when the user says "ship it", "wrap up", "commit this", or at the end of a multi-step task. Never pushes unless asked.
---

# Ship an update (verify → review → message → commit)

Run these four stages in order. Don't skip a stage because an earlier one went well; if a stage fails, fix the
problem (or stop and tell the user) before moving on. Report each stage's outcome honestly, with numbers.

## 1. Verify — prove it works

From the project root (`/Users/ganabattumur/GANA/projects/Automated Job App ` — note the trailing space; quote paths):

1. **Full test suite** (includes the ~15–25 s browser tests):
   ```
   JOBPREP_NO_SCHEDULER=1 .venv/bin/python -m pytest -q
   ```
   All must pass. If a test fails, find out whether the code or the test is wrong — never weaken a test just to pass.
2. **JavaScript syntax** (no Node on this Mac — use JavaScriptCore) for every changed `.js` file:
   ```
   osascript -l JavaScript -e 'ObjC.import("Foundation"); const src=$.NSString.stringWithContentsOfFileEncodingError($("<abs path>"),4,null).js; try{new Function(src);"ok"}catch(e){"SYNTAX ERROR: "+e}'
   ```
   For `extension/manifest.json`, check it parses as JSON.
3. **New behaviour has a test.** If the change added a feature or fixed a bug with no test covering it, add one
   (unit test in `tests/`, or extend `tests/test_ui_smoke.py` / `tests/test_extension_e2e.py` for UI/extension work).
   Tests use scratch data: `JOBPREP_DATA` / `JOBPREP_DB` env vars, the `db` fixture in `tests/test_sources_feed.py`.
   Never point tests at the real `jobs.db` or `data/`.
4. **Run it for real** when the change touches what the user sees:
   - UI: screenshot the affected pages with headless Playwright against the running server
     (`http://127.0.0.1:8000/#<page>`), look at the screenshots, and check for console errors.
   - Data/ranking/sources: exercise it on the real database **read-only** (separate process with
     `JOBPREP_NO_SCHEDULER=1`; don't change the user's saved settings — use `settings.get().model_copy(update=...)`).
   - Extension: live forms with a dummy profile on a scratch server; never click Submit on a real application.
   The user's own server usually runs with `--reload`, so it picks up backend edits by itself.

## 2. Review — read the diff like a reviewer

```
git status --short && git diff --stat && git diff
```

Check, and fix anything you find before committing:
- **Correctness:** edge cases, error handling at boundaries (network, parsing, missing files), off-by-one,
  timezones (dates stored as UTC; deadlines shown with `timeZone: 'UTC'`), duplicates, caching invalidation
  (`feed.invalidate()`, `resume.changed()`).
- **Scope:** only what was asked; no stray debug code (`console.log`, `print`, `JPDBG`), no leftover temp files.
- **Consistency:** matches surrounding style and comment density; README updated if a user-facing feature changed.
- **Privacy & secrets** — the GitHub repo is **public**:
  ```
  git diff --cached --name-only
  # your email/phone/name are read from the gitignored profile, so they never appear in this public file
  .venv/bin/python -c "import yaml,re,subprocess; p=yaml.safe_load(open('data/profile.yaml')) or {}; \
  terms=[t for t in (p.get('email'), p.get('phone'), re.sub(r'\D','',p.get('phone') or '')[-7:]) if t]; \
  pat='|'.join(map(re.escape,terms))+r'|app_password\s*=\s*[\"\x27]\w|api[_-]?key\s*='; \
  r=subprocess.run(['git','grep','--cached','-nIE',pat,'--','.'],capture_output=True,text=True); print(r.stdout or 'privacy check: clean')"
  ```
  Nothing from `data/`, `jobs.db*`, resumes, profile, Gmail settings, tokens or app passwords may be committed.
  Personal values belong in `data/` (gitignored), not in `config/` or code.
- **Honesty about limits:** anything not verified (e.g. needs the user's login) is noted for the final report.

## 3. Message — explain the change for future you

Write a commit message that someone reading `git log` in six months would understand:
- **Subject:** imperative, ≤ 72 chars, says what changed for the user ("Add …", "Fix …", "Make …").
- **Body:** blank line, then short bullets: what changed and *why*, notable decisions, bugs found and fixed along
  the way, and how it was verified (e.g. "106 tests pass; live: Greenhouse 22 fields").
- End with the attribution lines required by the current session instructions (e.g. `Co-Authored-By: …`).

## 4. Commit — keep the history

- Stage only the intended files by path (`git add <paths>`), never blindly `git add .` without checking status.
- Commit with the message via a heredoc: `git commit -F - <<'EOF' … EOF`.
- Keep history intact: **no** `--amend`, rebase, squash, reset, or force-push of existing commits. A follow-up fix
  is a new commit.
- Commit on the current branch (normally `main`). Do **not** push unless the user asks; then
  `git push origin main` (add `--tags` for release tags).
- For a milestone the user calls a version, also add an annotated tag (`git tag -a vX.Y.Z -F -`) with short release notes.

## Final report to the user

Short and concrete: what was verified (test counts, live results), what the review caught and fixed, the commit
hash + subject, anything not verified and why, and the push command if they want it on GitHub.
